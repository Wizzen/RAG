"""Downtime charts: local ECharts, lazy loading and drill-down wiring."""

import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

STATIC = Path(settings.BASE_DIR) / "static" / "operations_workbench"
ECHARTS = STATIC / "vendor" / "echarts-5.6.0" / "echarts.esm.min.mjs"


class EchartsAssetTests(SimpleTestCase):
    def test_vendored_echarts_matches_recorded_checksum(self):
        attribution = (STATIC / "vendor" / "ATTRIBUTION.txt").read_text(encoding="utf-8")
        recorded = re.search(r"echarts\.esm\.min\.mjs.*?SHA-256 ([0-9a-f]{64})", attribution, re.S).group(1)
        self.assertEqual(hashlib.sha256(ECHARTS.read_bytes()).hexdigest(), recorded)
        self.assertIn("Apache ECharts 5.6.0", attribution)
        self.assertTrue((ECHARTS.parent / "LICENSE").exists())
        self.assertTrue(finders.find("operations_workbench/vendor/echarts-5.6.0/echarts.esm.min.mjs"))

    def test_charts_module_loads_only_local_code(self):
        charts = (STATIC / "charts.mjs").read_text(encoding="utf-8")
        self.assertIn("await import('./vendor/echarts-5.6.0/echarts.esm.min.mjs')", charts)
        urls = set(re.findall(r"https?://[^\s'\"]+", charts))
        self.assertEqual(urls, {"http://www.w3.org/2000/svg"})

    def test_charts_are_lazy_loaded_with_the_page_version(self):
        entry = (STATIC / "workbench.mjs").read_text(encoding="utf-8")
        self.assertIn("import('./charts.mjs' + version)", entry)
        start = entry.index("await Promise.all(")
        self.assertNotIn("charts.mjs", entry[start:entry.index(";", start)])


class ChartWiringTests(SimpleTestCase):
    def setUp(self):
        self.charts = (STATIC / "charts.mjs").read_text(encoding="utf-8")
        self.entry = (STATIC / "workbench.mjs").read_text(encoding="utf-8")

    def test_every_chart_drill_kind_is_accepted_by_the_server(self):
        from operations_workbench import sources

        allowed = {item["value"] for item in sources.DRILL_KINDS}
        used = set(re.findall(r"drill\('(\w+)'", self.charts))
        self.assertEqual(used, {"day", "attraction", "code", "reason", "asset"})
        self.assertLessEqual(used, allowed)

    def test_required_charts_and_interactions_exist(self):
        for text in ("type: 'bar'", "type: 'line', yAxisIndex: 1", "type: 'pie', radius: ['45%', '70%']",
                     "type: 'heatmap'", "getDataURL({type: 'png'", "trigger: 'axis'", "ResizeObserver",
                     "MutationObserver", "attributeFilter: ['data-theme'", "dimension: 2"):
            self.assertIn(text, self.charts)
        self.assertIn("comparison?.totals", self.charts)

    def test_drill_keeps_chart_scope_and_only_requeries_events(self):
        self.assertIn("function scopeKey(params)", self.entry)
        self.assertIn("if (chartView?.key !== key)", self.entry)
        self.assertIn("drill_kind: kind, drill_key: String(key)", self.entry)
        self.assertIn("'清除筛选'", self.entry)
        self.assertIn("showSummary(view.summary, node)", self.entry, "图表载入失败时回退到统计表")
