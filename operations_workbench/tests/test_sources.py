"""Regression cases for the read-only source dispatch boundary."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from operations_workbench import sources, sources_splunk


class SourceTests(SimpleTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        override = override_settings(OPERATIONS_RUNTIME_DIR=self.folder)
        override.enable()
        self.addCleanup(override.disable)

    def make_snapshot(self):
        with closing(sqlite3.connect(self.folder / "downtime.sqlite3")) as conn:
            conn.executescript("""
                CREATE TABLE months(month TEXT, synced_through TEXT, synced_at TEXT, row_count INTEGER);
                CREATE TABLE location_ancestors(location TEXT,ancestor TEXT);
                CREATE TABLE workorders(workorderid INTEGER PRIMARY KEY, wonum TEXT, location TEXT, assetnum TEXT,
                    worktype TEXT, downtime_code TEXT, historyflag INTEGER, attraction_code TEXT, reason_code TEXT,
                    reason_description TEXT, report_local TEXT, report_day TEXT, downtime_minutes REAL, ongoing INTEGER,
                    cust_maint_down INTEGER);
                INSERT INTO months VALUES('2026-01','2026-01-31','2026-02-01',1);
                INSERT INTO workorders VALUES(12,'T100','PUMP-32','QA-PUMP','EM','100',0,'SDLFLSDMT','01',
                    '检查记录','2026-01-31 12:00:00','2026-01-31',7.5,0,1);
            """)

    def test_read_only_snapshot_search_summary_detail_history_and_coverage(self):
        self.make_snapshot()
        initial = (self.folder / "downtime.sqlite3").read_bytes()
        paths = ["downtime.search", "downtime.dashboard", "downtime.history"]
        for action in paths:
            data = sources.run_action(
                action, {"assetnum": "QA-PUMP"} if action.endswith("history") else {}
            )
            self.assertEqual(data["items"][0]["workorderid"], "12")
            self.assertEqual(data["summary"]["totals"]["minutes"], 7.5)
            self.assertEqual(data["cache"]["data_through"], "2026-01-31")
        detail = sources.run_action("downtime.detail", {"workorderid": "12"})
        self.assertEqual(detail["workorder"]["wonum"], "T100")
        with self.assertRaises(sources.SourceError) as failure:
            sources.run_action(
                "downtime.dashboard",
                {"start_date": "2026-02-01", "end_date": "2026-02-02"},
            )
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual((self.folder / "downtime.sqlite3").read_bytes(), initial)

    def test_absent_snapshot_is_not_zero_observed_events(self):
        with self.assertRaises(sources.SourceError) as failure:
            sources.run_action("downtime.search", {})
        self.assertEqual(409, failure.exception.status)
        self.assertFalse((self.folder / "downtime.sqlite3").exists())

    def test_live_worker_requires_a_known_action_and_never_receives_caller_endpoints(
        self,
    ):
        with patch("operations_workbench.sources.subprocess.run") as launch:
            for action, params in [
                ("http.get", {}),
                ("splunk.search", {"search": "| delete"}),
                (
                    "maximo.workorder",
                    {"wonum": "T123", "url": "https://arbitrary.invalid"},
                ),
                ("splunk.probe", {"server": "other"}),
            ]:
                with self.assertRaises(sources.SourceError):
                    sources.run_action(action, params)
            launch.assert_not_called()
            launch.return_value.returncode = 0
            launch.return_value.stdout = json.dumps({"ok": True, "data": {"items": []}})
            sources.run_action("maximo.workorder", {"wonum": "T123"})
            self.assertEqual(
                launch.call_args.args[0][-1], "operations_workbench.source_worker"
            )
            self.assertEqual(
                json.loads(launch.call_args.kwargs["input"]),
                {"action": "maximo.workorder", "params": {"wonum": "T123"}},
            )
            self.assertNotIn("DJANGO_SETTINGS_MODULE", launch.call_args.kwargs["env"])

    def test_actual_worker_does_not_auto_load_main_site_runtime_or_read_its_sources(
        self,
    ):
        # Malformed event filter must be rejected before transport even with no local credentials.
        with self.assertRaises(sources.SourceError) as failure:
            sources.run_action("splunk.search", {"keyword": "bad | outputlookup"})
        self.assertEqual(failure.exception.status, 400)
        with self.assertRaises(sources.SourceError) as failure:
            sources.run_action("splunk.probe", {})
        self.assertEqual(failure.exception.status, 503)

    def test_splunk_query_uses_closed_fields_and_handles_partial_last_page(self):
        rows = [
            {"apexmssgtext": "正常", "_time": "2026-01-01"},
            {"_raw": 'attrname="Train" apexmssgtext="联锁"'},
        ]
        with patch.object(
            sources_splunk, "_request", return_value={"results": rows}
        ) as http:
            result = sources_splunk.search(
                {
                    "start_time": "2026-01-01T09:30",
                    "end_time": "2026-01-01T10:00",
                    "target": "MTR1,MTR2",
                    "keyword": "door",
                    "limit": 2,
                    "offset": 4,
                }
            )
        payload = http.call_args.kwargs["data"]
        self.assertEqual(http.call_args.args, ("POST", "/services/search/jobs"))
        self.assertEqual(payload["exec_mode"], "oneshot")
        self.assertIn(
            'apexmssgtarget="MTR1" OR apexmssgtarget="MTR2"', payload["search"]
        )
        self.assertIn("where _ops_row>4", payload["search"])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["items"][1]["apexmssgtext"], "联锁")

    def test_downtime_log_window_scoped_to_its_attraction(self):
        with patch.object(
            sources_splunk, "_request", return_value={"results": []}
        ) as query:
            data = sources_splunk.linked_search(
                {
                    "location": "SDLFLSDMT-X",
                    "report_local": "2026-10-21 10:00:00",
                    "downtime_end_local": "2026-10-21 10:15:00",
                    "padding_minutes": 5,
                }
            )
        self.assertEqual(data["window"]["window_start_local"], "2026-10-21 09:55:00")
        self.assertIn(
            "SDR_SDL_Seven Dwarfs Mine Train", query.call_args.kwargs["data"]["search"]
        )

    def test_original_print_layouts_render_pdf_for_both_detail_types(self):
        for kind, key in [("workorder", "wonum"), ("jobplan", "jpnum")]:
            with patch.object(
                sources, "run_action", return_value={kind: {key: "QA-001"}, "tasks": []}
            ):
                self.assertTrue(sources.render_pdf(kind, "QA-001").startswith(b"%PDF-"))
