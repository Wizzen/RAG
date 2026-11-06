import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import include, path

from vehicle_tracker.equipment import registry


def pump_view(request, pump_id):
    return HttpResponse(pump_id)


urlpatterns = [
    path("tools/", include("vehicle_tracker.urls")),
    path("tools/pumps/<slug:pump_id>/", pump_view, name="pump_view"),
]


@override_settings(ROOT_URLCONF=__name__)
class EquipmentRegistryTests(SimpleTestCase):
    def test_second_device_retains_parts_and_only_uses_registered_viewer(self):
        pump = {"format": "shdr-equipment-model", "version": 1, "id": "pump-07", "name": "循环水泵",
                "components": [{"id": "motor", "name": "电机"}],
                "viewer_url": "https://example.invalid/legacy/tool"}
        with TemporaryDirectory() as directory, override_settings(EQUIPMENT_MODEL_DIR=directory):
            (Path(directory)/"pump.json").write_text(json.dumps(pump))
            self.assertIsNone(registry()["models"][1]["viewer_url"])
            with override_settings(EQUIPMENT_MODEL_VIEWERS={"pump-07": {"route":"pump_view", "kwargs":{"pump_id":"pump-07"}}}):
                model = registry()["models"][1]
                self.assertEqual(model["viewer"], "components")
                self.assertEqual(model["viewer_url"], "/tools/pumps/pump-07/")
                self.assertEqual(model["manifest"]["components"][0]["id"], "motor")
            with override_settings(EQUIPMENT_MODEL_VIEWERS={"pump-07": {"route": "missing"}}):
                self.assertIsNone(registry()["models"][1]["viewer_url"])
