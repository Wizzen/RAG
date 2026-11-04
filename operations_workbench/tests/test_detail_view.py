"""Work-order detail redesign: field labels, UTC/local de-duplication and versioned module loading."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from operations_workbench.vendor import maximo_workbench as maximo

STATIC = Path(settings.BASE_DIR) / "static" / "operations_workbench"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def columns(sql):
    """Output column names of a SELECT list (aliases and bare table columns)."""
    select = sql.split("FROM maximo.workorder w")[0].split("FROM maximo.wotask t")[0].split("FROM maximo.wostatus s")[0]
    found = re.findall(r"\bAS\s+(\w+)\s*,?\s*$", select, re.M | re.I)
    found += re.findall(r"^\s*(?:SELECT\s+)?[a-z]\.(\w+)\s*,?\s*$", select, re.M | re.I)
    return set(found)


def labels():
    source = read("presentation.mjs")
    block = source[source.index("const labels = {"):source.index("};", source.index("const labels = {"))]
    return dict(re.findall(r"(\w+): '([^']+)'", block))


class FieldLabelTests(SimpleTestCase):
    def test_every_work_order_detail_column_has_a_chinese_label(self):
        names = labels()
        for sql in (maximo.SQL_WO_DETAIL, maximo.SQL_WO_TASKS, maximo.SQL_WO_STATUS):
            found = columns(sql)
            self.assertGreater(len(found), 3)
            missing = sorted(name for name in found if name not in names)
            self.assertEqual(missing, [], "缺少中文字段名")
            for name in found:
                self.assertRegex(names[name], r"[\u4e00-\u9fff]", name)
        self.assertIn("location_description", columns(maximo.SQL_WO_DETAIL))

    def test_task_number_and_task_id_are_distinguishable(self):
        names = labels()
        self.assertNotEqual(names["taskid"], names["jobtaskid"])

    def test_utc_and_site_time_pairs_exist_in_the_query(self):
        source = read("presentation.mjs")
        pairs = dict(re.findall(r"(\w+): '(\w+_local)'", source[source.index("RAW_TIME_PAIRS"):]))
        self.assertEqual(set(pairs), {"targstartdate", "targcompdate", "pmduedate"})
        found = columns(maximo.SQL_WO_DETAIL)
        for raw, local in pairs.items():
            self.assertIn(raw, found)
            self.assertIn(local, found)

    def test_hours_and_dates_are_formatted(self):
        source = read("presentation.mjs")
        for name in ("estdur", "estlabhrs", "cust_wrenchtime"):
            self.assertIn(f"'{name}'", source[source.index("HOUR_FIELDS"):source.index("PRECISE_FIELDS")])
        self.assertIn("Number(hours.toFixed(2)) + ' h'", source)
        self.assertIn("return match[1] + ' ' + match[2]", source)
        self.assertIn("timeZone: 'Asia/Shanghai'", source)


class ModuleLoadingTests(SimpleTestCase):
    def test_submodules_load_with_the_page_asset_version(self):
        entry = read("workbench.mjs")
        self.assertNotRegex(entry, r"^import\s", "入口脚本不应静态导入子模块")
        self.assertIn("const version = new URL(import.meta.url).search;", entry)
        for module in ("./ai-history.mjs", "./presentation.mjs", "./workorder-view.mjs"):
            self.assertIn(f"'{module}'", entry)
            self.assertTrue((STATIC / module[2:]).exists())
        view = read("workorder-view.mjs")
        self.assertNotRegex(view, r"(?m)^import\s")
        self.assertIn("await import('./presentation.mjs' + new URL(import.meta.url).search)", view)

    def test_detail_styles_use_theme_tokens_only(self):
        css = read("detail.css")
        self.assertNotRegex(css, r"https?://")
        colours = re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
        self.assertEqual(set(colours), {"#fff"})
        self.assertIn("@media (max-width: 420px)", css)

    def test_view_covers_sticky_header_groups_tasks_and_history(self):
        view = read("workorder-view.mjs")
        for text in ("'描述'", "'计划时间'", "'工时'", "'其他字段', false", "statusBadge",
                     "'全部展开'", "'全部收起'", "'紧凑表格'", "'显示英文'", "最新在前", "'Task '"):
            self.assertIn(text, view)
        self.assertIn(".sort((a, b) => a.order - b.order)", view)
        self.assertIn("localeCompare(String(a.when))", view)
        self.assertIn("position: sticky", read("detail.css"))


class PageTests(TestCase):
    def test_page_links_detail_styles_with_asset_version(self):
        self.client.force_login(get_user_model().objects.create_user(username="detail-reader", is_staff=True))
        page = self.client.get("/tools/operations/").content.decode()
        self.assertRegex(page, r'operations_workbench/detail\.css\?v=[0-9a-f]{12}')
