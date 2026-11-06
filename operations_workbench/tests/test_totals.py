"""Exact totals, server-side aggregates, paging and full export instead of row-limited samples."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from operations_workbench import ai, sources, sources_splunk
from operations_workbench.vendor import maximo_workbench as maximo

ROOT = "/tools/operations/"
WO_COLUMNS = ["wonum", "workorderid", "status", "worktype", "persongroup", "location", "row_number_"]


def maximo_db(total=137, page=None):
    """Stand-in for the Oracle driver: page rows for the paged query, GROUPING SETS rows for the summary."""
    calls = []

    def execute(sql, binds):
        calls.append((sql, binds))
        if "GROUPING SETS" in sql:
            rows = [("total", None, None, None, None, None, str(total)),
                    ("status", "WSCH", None, None, None, None, "100"),
                    ("status", "COMP", None, None, None, None, str(total - 100)),
                    ("persongroup", None, None, "AES213", None, None, str(total)),
                    ("location", None, None, None, "SDLFLSDMT-RV03", None, "90"),
                    ("location", None, None, None, "SDLFLSDMT", None, str(total - 90)),
                    ("report_day", None, None, None, None, "2026-10-31", str(total))]
            return ["group_kind", "status", "worktype", "persongroup", "location", "report_day", "row_count"], rows
        start, end = int(binds["row_offset"]), int(binds["row_end"])
        rows = [(str(7824203 - i), str(69942673 - i), "WSCH", "PM", "AES213", "SDLFLSDMT", str(i + 1))
                for i in range(start, min(end, total if page is None else page))]
        return WO_COLUMNS, rows

    return execute, calls


class MaximoTotalsTests(SimpleTestCase):
    def test_total_and_facets_cover_every_match_not_just_the_page(self):
        execute, calls = maximo_db()
        with patch.object(maximo, "_execute", side_effect=execute):
            data = maximo.search_workorders({"keyword": "小矮人", "persongroups": ["AES213"], "limit": 50,
                                             "start_date": "2026-10-31", "end_date": "2026-10-31"})
        self.assertEqual((data["count"], data["total"]), (50, 137))
        self.assertTrue(data["truncated"] and data["has_more"])
        self.assertEqual(data["facets"]["status"], {"WSCH": 100, "COMP": 37})
        self.assertEqual(data["facets"]["attraction"], {"七个小矮人矿山飞车": 137})
        self.assertNotIn("row_number_", data["items"][0])
        page_sql, page_binds = next(call for call in calls if "GROUPING SETS" not in call[0])
        summary_sql, summary_binds = next(call for call in calls if "GROUPING SETS" in call[0])
        # Both statements apply the same filters; each receives only the binds it names.
        for sql, binds in ((page_sql, page_binds), (summary_sql, summary_binds)):
            self.assertIn("lw.description LIKE :keyword_pattern", sql)
            self.assertIn("w.persongroup IN (:pg_0)", sql)
            self.assertEqual(binds["pg_0"], "AES213")
        self.assertEqual((page_binds["row_offset"], page_binds["row_end"]), (0, 50))
        self.assertNotIn("row_offset", summary_binds)

    def test_offset_pages_through_the_full_match(self):
        execute, _ = maximo_db()
        with patch.object(maximo, "_execute", side_effect=execute):
            last = maximo.search_workorders({"limit": 50, "offset": 100})
        self.assertEqual((last["offset"], last["count"], last["has_more"]), (100, 37, False))
        self.assertEqual(last["items"][0]["wonum"], str(7824203 - 100))

    def test_exactly_the_limit_is_not_reported_as_truncated(self):
        execute, _ = maximo_db(total=50)
        with patch.object(maximo, "_execute", side_effect=execute):
            data = maximo.search_workorders({"limit": 50})
        self.assertFalse(data["truncated"] or data["has_more"])

    def test_page_cap_applies_to_the_form_but_not_to_export(self):
        execute, _ = maximo_db(total=6000)
        with patch.object(maximo, "_execute", side_effect=execute):
            with self.assertRaises(maximo.DowntimeSearchError):
                maximo.search_workorders({"limit": 600})
            self.assertEqual(maximo.search_workorders({"limit": 5000}, max_limit=5000)["count"], 5000)

    def test_jobplan_search_counts_latest_revisions(self):
        calls = []

        def execute(sql, binds):
            calls.append(sql)
            if "GROUPING SETS" in sql:
                return ["group_kind", "status", "cust_craft", "cust_jpfreq", "row_count"], [
                    ("total", None, None, None, "220"), ("craft", None, "MECH", None, "220")]
            return ["jpnum", "rev_rank", "row_number_"], [("JPL4072004", "1", "1")]

        with patch.object(maximo, "_execute", side_effect=execute):
            data = maximo.search_jobplans({"jpnum": "JPL*", "limit": 1})
        self.assertEqual((data["total"], data["facets"]["craft"]), (220, {"MECH": 220}))
        self.assertTrue(all("rev_rank = 1" in sql for sql in calls))
        self.assertEqual(set(data["items"][0]), {"jpnum"})


class SnapshotTotalsTests(SimpleTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        override = override_settings(OPERATIONS_RUNTIME_DIR=self.folder)
        override.enable()
        self.addCleanup(override.disable)
        with closing(sqlite3.connect(self.folder / "downtime.sqlite3")) as conn:
            conn.executescript("""
                CREATE TABLE months(month TEXT, synced_through TEXT, synced_at TEXT, row_count INTEGER);
                CREATE TABLE location_ancestors(location TEXT,ancestor TEXT);
                CREATE TABLE workorders(workorderid INTEGER PRIMARY KEY, wonum TEXT, location TEXT, assetnum TEXT,
                    worktype TEXT, downtime_code TEXT, historyflag INTEGER, attraction_code TEXT, reason_code TEXT,
                    reason_description TEXT, report_local TEXT, report_day TEXT, downtime_minutes REAL, ongoing INTEGER,
                    cust_maint_down INTEGER);
                INSERT INTO months VALUES('2026-09','2026-09-30','2026-10-01',1),('2026-10','2026-10-26','2026-10-27',1);
            """)
            rows = []
            for i in range(57):
                day = 20 + i % 7
                rows.append((i + 1, f"W{i}", "SDLFLSDMT-RV" if i % 2 else "SDLBTBRR", "A1" if i % 3 else "A2", "EM",
                             ["100", "101", "105"][i % 3], 0, "SDLFLSDMT" if i % 2 else "SDLBTBRR", "10", "车辆",
                             f"2026-10-{day} 10:{i % 60:02d}:00", f"2026-10-{day}", 60.0, 0, 0))
            rows.append((99, "W99", "SDLBTBRR", "A1", "EM", "100", 0, "SDLBTBRR", "10", "车辆",
                         "2026-10-15 09:00:00", "2026-10-15", 30.0, 0, 0))
            conn.executemany("INSERT INTO workorders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()

    def test_events_report_exact_total_page_by_offset_and_full_breakdowns(self):
        window = {"start_date": "2026-10-20", "end_date": "2026-10-26"}
        first = sources.run_action("downtime.search", {**window, "limit": 50})
        rest = sources.run_action("downtime.search", {**window, "limit": 50, "offset": 50})
        self.assertEqual((first["total"], first["count"], first["has_more"]), (57, 50, True))
        self.assertEqual((rest["count"], rest["has_more"]), (7, False))
        self.assertFalse({row["workorderid"] for row in first["items"]} & {row["workorderid"] for row in rest["items"]})
        summary = first["summary"]
        self.assertEqual(summary["totals"]["minutes"], 3420)
        self.assertEqual(sum(cell["event_count"] for cell in summary["heatmap"]), 57)
        self.assertEqual({row["key"] for row in summary["assets"]}, {"A1", "A2"})
        self.assertEqual(first["comparison"]["start_date"], "2026-10-13")
        self.assertEqual(first["comparison"]["totals"]["event_count"], 1)

    def test_chart_drill_filters_events_and_needs_a_known_kind(self):
        params = {"start_date": "2026-10-20", "end_date": "2026-10-26", "limit": 100}
        drilled = sources.run_action("downtime.search", {**params, "drill_kind": "asset", "drill_key": "A2"})
        self.assertEqual(drilled["total"], 19)
        self.assertTrue(all(row["assetnum"] == "A2" for row in drilled["items"]))
        self.assertIsNone(drilled["comparison"])
        with self.assertRaises(sources.SourceError):
            sources.run_action("downtime.search", {**params, "drill_kind": "sql", "drill_key": "x"})

    def test_every_chart_bar_drills_to_exactly_its_own_events(self):
        params = {"start_date": "2026-10-15", "end_date": "2026-10-26"}
        summary = sources.run_action("downtime.dashboard", params)["summary"]
        targets = {"day": "daily", "attraction": "attractions", "reason": "reasons", "code": "codes", "asset": "assets"}
        for kind, target in targets.items():
            rows = [row for row in summary[target] if row["event_count"]]
            self.assertTrue(rows, target)
            for row in rows:
                drilled = sources.run_action("downtime.dashboard", {**params, "drill_kind": kind, "drill_key": row["key"]})
                self.assertEqual(drilled["total"], row["event_count"], (kind, row["key"]))
        cells = sum(cell["event_count"] for cell in summary["heatmap"])
        self.assertEqual(cells, summary["totals"]["event_count"])

    def test_previous_window_has_equal_length_and_ends_the_day_before(self):
        from operations_workbench.vendor import downtime_insights as insights

        self.assertEqual(insights.previous_window({"start_date": "2026-10-20", "end_date": "2026-10-26"}),
                         ("2026-10-13", "2026-10-19"))
        self.assertEqual(insights.previous_window({"start_date": "2026-03-01", "end_date": "2026-03-31"}),
                         ("2026-01-29", "2026-02-28"))

    def test_previous_period_outside_the_snapshot_has_no_comparison(self):
        data = sources.run_action("downtime.search", {"start_date": "2026-09-01", "end_date": "2026-10-26"})
        self.assertIsNone(data["comparison"])

    def test_export_returns_every_row_up_to_the_cap(self):
        data = sources.export("downtime.search", {"start_date": "2026-10-01", "end_date": "2026-10-26", "limit": 5})
        self.assertEqual((data["exported"], data["total"], data["capped"]), (58, 58, False))


class LiveDowntimeTotalsTests(SimpleTestCase):
    def test_live_events_take_total_and_summary_from_the_aggregate_query(self):
        from operations_workbench.vendor import downtime_insights as insights, downtime_search as db

        summary = {"totals": {"event_count": 812}, "daily": []}
        with patch.object(db, "search_downtime", return_value={"items": [{"wonum": "1"}] * 50, "count": 50}) as rows, \
                patch.object(insights, "summary_only", return_value=summary) as aggregate:
            data = sources._run_direct("downtime.search", {"source": "live", "start_date": "2026-10-01",
                                                           "end_date": "2026-10-26", "offset": 50})
        self.assertEqual((data["total"], data["has_more"], data["offset"]), (812, True, 50))
        self.assertEqual(rows.call_args.args[0]["offset"], 50)
        self.assertIs(aggregate.call_args.args[0]["include_history"], False)


class ExplainDigestTests(SimpleTestCase):
    def setUp(self):
        patch.object(ai, "model_configuration", return_value={}).start()
        self.addCleanup(patch.stopall)

    def test_model_gets_exact_total_server_aggregates_and_few_samples(self):
        execute, _ = maximo_db()
        with patch.object(maximo, "_execute", side_effect=execute):
            data = maximo.search_workorders({"limit": 50})
        for row in data["items"]:
            row["description_en"] = "MRM SDMT RV UNDERSIDE PM " * 20
        plan = {"calls": [{"action": "maximo.workorders", "params": {"keyword": "小矮人"}}]}
        catalog = {"actions": [{"id": "maximo.workorders", "source": "maximo", "label": "工单检索",
                                "fields": [{"name": "keyword", "type": "text"}]}], "sources": []}
        with patch.object(sources, "catalog", return_value=catalog), \
                patch.object(sources, "run_action", return_value=data), \
                patch.object(ai, "call_model", side_effect=[json.dumps(plan), "共 137 条。"]) as model:
            result = ai.ask({"question": "10月31日小矮人的PM工单按状态统计"})
        explain = json.loads(model.call_args.args[1][1]["content"])
        evidence = explain["evidence"][0]["result"]
        self.assertEqual(evidence["result_summary"]["total_matches"], 137)
        self.assertEqual(evidence["server_aggregates"]["status"], {"WSCH": 100, "COMP": 37})
        self.assertLessEqual(len(evidence["sample_rows"]), ai.SAMPLE_ROWS)
        self.assertLessEqual(len(json.dumps(evidence, ensure_ascii=False)), ai.EVIDENCE_BUDGET)
        self.assertIn("server_aggregates", ai.EXPLAIN)
        span = result["evidence"][0]["coverage"]
        self.assertEqual((span["total"], span["returned"], span["truncated"]), (137, 50, True))
        self.assertIn("景点", span["suggestions"])
        self.assertEqual(len(result["evidence"][0]["data"]["items"]), 50)

    def test_complete_results_carry_no_narrowing_hint(self):
        span = ai.coverage("maximo.workorders", {}, {"items": [{}] * 3, "total": 3})
        self.assertEqual((span["truncated"], span["suggestions"]), (False, []))
        self.assertIsNone(ai.coverage("maximo.workorder", {}, {"workorder": {}}))


class ExportEndpointTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="export-reader", is_staff=True))

    def post(self, body):
        return self.client.post(ROOT + "api/export/", body, content_type="application/json")

    def test_export_is_capped_and_reports_the_true_total(self):
        rows = [{"wonum": str(i)} for i in range(6000)]
        with patch.object(sources, "run_action", return_value={"items": rows, "total": 12000}) as run:
            data = self.post({"action": "maximo.workorders", "params": {"keyword": "泵"}}).json()
        run.assert_called_once_with("maximo.workorders", {"keyword": "泵"}, export=True)
        self.assertEqual((data["exported"], data["total"], data["cap"], data["capped"]), (5000, 12000, 5000, True))
        self.assertEqual(len(data["items"]), 5000)

    def test_page_offers_full_export_next_to_batch_export(self):
        page = self.client.get(ROOT).content.decode()
        self.assertIn('id="ops-export-all"', page)
        self.assertIn("5000", page)

    def test_only_list_queries_are_exportable(self):
        with patch("operations_workbench.sources.subprocess.run") as launch:
            response = self.post({"action": "maximo.workorder", "params": {"wonum": "7824203"}})
            launch.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(ROOT + "api/export/").status_code, 405)

    def test_export_worker_reads_from_offset_zero_with_the_export_cap(self):
        with patch.object(maximo, "search_workorders", return_value={"items": []}) as search:
            sources._run_direct("maximo.workorders", {"keyword": "泵", "limit": 20, "offset": 40}, export=True)
        self.assertEqual(search.call_args.args[0], {"keyword": "泵", "limit": 5000, "offset": 0})
        self.assertEqual(search.call_args.kwargs, {"max_limit": 5000})
        with patch.object(sources_splunk, "_request", return_value={"results": []}) as http:
            sources._run_direct("splunk.search", {"start_time": "2026-11-04T08:00", "end_time": "2026-11-04T09:00"},
                                export=True)
        page = next(call for call in http.call_args_list if "| fields " in call.kwargs["data"]["search"])
        self.assertEqual(page.kwargs["data"]["count"], "5001")
        self.assertEqual(page.kwargs["max_response"], sources_splunk.EXPORT_RESPONSE)
