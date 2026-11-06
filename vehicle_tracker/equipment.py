"""Model library registry. Published manifests are curated, site-wide templates."""
import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

logger = logging.getLogger(__name__)
LIMIT = 2 * 1024 * 1024


def registered_viewer(model_id):
    """Only deployment-owned route names can attach a viewer to a shared model."""
    binding = getattr(settings, 'EQUIPMENT_MODEL_VIEWERS', {}).get(model_id)
    if not isinstance(binding, dict) or not isinstance(binding.get('route'), str):
        return None
    try:
        url = reverse(binding['route'], kwargs=binding.get('kwargs', {}))
    except (NoReverseMatch, TypeError, ValueError):
        logger.warning('Invalid equipment viewer registration for model %s', model_id)
        return None
    return url if url.startswith('/') and not url.startswith('//') else None


def registry():
    models = [{
        "id": "sdmt", "name": "七个小矮人矿山车", "category": "游乐设备",
        "description": "查看列车三维模型、部件资料与历次检验记录。",
        "viewer": "sdmt", "url": reverse("vehicle_tracker:sdmt"),
        "features": ["三维模型", "部件追踪", "图纸原文", "车辆 AI"],
    }]
    # Only files intentionally published by the operator belong in this folder.
    directory = getattr(settings, "EQUIPMENT_MODEL_DIR", Path(settings.DATA_DIR) / "equipment_models")
    seen = {"sdmt"}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            with path.open("rb") as source:
                raw = source.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError("manifest exceeds size limit")
            manifest = json.loads(raw)
            if (not isinstance(manifest, dict) or manifest.get("format") != "shdr-equipment-model"
                    or type(manifest.get("version")) is not int or manifest["version"] != 1
                    or not isinstance(manifest.get("id"), str) or manifest["id"] in seen
                    or not isinstance(manifest.get("name"), str)
                    or not isinstance(manifest.get("components"), list)):
                raise ValueError("invalid model manifest or duplicate model ID")
            seen.add(manifest["id"])
            models.append({"viewer": "components", "manifest": manifest,
                           "viewer_url": registered_viewer(manifest['id'])})
        except (OSError, ValueError, RecursionError) as exc:
            logger.warning("Ignored equipment manifest %s: %s", path.name, exc)
    return {"schema_version": 1, "models": models}


@login_required
@require_safe
@never_cache
def library(request):
    return render(request, "vehicle_tracker/equipment.html", {"registry": registry()})


@login_required
@require_safe
@never_cache
def catalog(request):
    return JsonResponse(registry())
