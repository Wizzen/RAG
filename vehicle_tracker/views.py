"""Django hosting for the copied tracker; no RAG models or migrations are added."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST, require_safe

from . import integrations


def asset_version() -> str:
    root = Path(settings.BASE_DIR) / "static" / "vehicle_tracker"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


@login_required
@require_safe
@ensure_csrf_cookie
@never_cache
def index(request):
    version = asset_version()
    imports = {"three": static("vehicle_tracker/vendor/three/three.module.min.js")}
    for name in ("core", "model", "pointer", "report", "pdf", "ai", "ai-context", "documents", "inline"):
        path = static(f"vehicle_tracker/js/vehicle-tracker-{name}.mjs")
        imports[path] = f"{path}?v={version}"
    return render(request, "vehicle_tracker/index.html", {
        "asset_version": version,
        "import_map_json": json.dumps({"imports": imports}),
        "vehicle_workspace": True,
    })


@require_POST
@never_cache
def qaqc_detail(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "登录已失效，请刷新页面并重新登录。"}, status=401)
    try:
        if len(request.body) > 4096:
            raise ValueError("查询内容过长。")
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError("查询内容必须是 JSON 对象。")
        code = integrations.validate_code(payload.get("part_no"))
    except (ValueError, UnicodeDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    try:
        data, status = integrations.part_detail(code)
    except integrations.ToolboxUnavailable as exc:
        return JsonResponse({"detail": str(exc)}, status=503)
    return JsonResponse(data, status=status)


@require_POST
@never_cache
def ai_analyze(request):
    from . import ai
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "登录已失效，请刷新页面并重新登录。"}, status=401)
    try:
        if len(request.body) > ai.MAX_BODY:
            raise ai.AnalysisError("本次资料过多，请缩小分析范围。")
        payload = json.loads(request.body)
        return JsonResponse(ai.analyze(payload))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "提问内容不是有效JSON。"}, status=400)
    except ai.AnalysisError as exc:
        return JsonResponse({"detail": str(exc)}, status=exc.status)


@login_required
@require_safe
@never_cache
def external(request, destination):
    if destination not in integrations.DESTINATIONS:
        raise Http404
    code = request.GET.get("code", "")
    try:
        integrations.validate_code(code)
    except ValueError as exc:
        return render(request, "vehicle_tracker/connection.html", {
            "message": str(exc), "code": code,
        }, status=400)
    except integrations.ToolboxUnavailable as exc:
        return render(request, "vehicle_tracker/connection.html", {
            "message": str(exc), "code": code,
        }, status=503)
    from django.urls import reverse
    from urllib.parse import urlencode
    return HttpResponseRedirect(reverse("vehicle_tracker:index") + "?" + urlencode({"inspect": destination, "code": code}))


@require_safe
@never_cache
def drawing_documents(request, code):
    from . import documents
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "请先登录。"}, status=401)
    try:
        return JsonResponse(documents.search(request.user, code))
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


@require_safe
@never_cache
def document_location(request, doc_id):
    from . import documents
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "请先登录。"}, status=401)
    try:
        number = int(request.GET.get("page", "0"))
        data = documents.page_metadata(request.user, doc_id, request.GET.get("drawing", ""), number, request.GET.get("version", ""))
        return JsonResponse(data)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


@require_safe
@never_cache
def document_page(request, doc_id):
    from . import documents
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "请先登录。"}, status=401)
    try:
        number = int(request.GET.get("page", "0"))
        content = documents.render_page(request.user, doc_id, number, request.GET.get("version", ""))
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    response = HttpResponse(content, content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_safe
@never_cache
def inline_record(request, kind, code):
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "请先登录。"}, status=401)
    try:
        if kind == "workorder-pdf":
            content = integrations.workorder_pdf(code)
            response = HttpResponse(content, content_type="application/pdf")
            response["Content-Disposition"] = 'inline; filename="workorder.pdf"'
            response["X-Content-Type-Options"] = "nosniff"
            return response
        return JsonResponse(integrations.record_detail(kind, code))
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    except integrations.ToolboxUnavailable as exc:
        return JsonResponse({"detail": str(exc)}, status=503)
