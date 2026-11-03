"""Authenticated, same-origin operations pages and read-only queries."""
from functools import wraps
import hashlib
import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from fos_rag.isolated_model import AnalysisError
from . import ai, sources

ACCESS_GROUP = "运维数据工作台"
logger = logging.getLogger(__name__)


def can_query(user):
    return user.is_staff or user.is_superuser or user.groups.filter(name=ACCESS_GROUP).exists()


def api(view):
    @wraps(view)
    @never_cache
    def guarded(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"detail": "请先登录工作台。"}, status=401)
        if not can_query(request.user):
            return JsonResponse({"detail": "此账号需要运维数据工作台访问权限。"}, status=403)
        try:
            return view(request, *args, **kwargs)
        except (sources.SourceError, AnalysisError) as exc:
            return JsonResponse({"detail": str(exc)}, status=exc.status)
        except Exception as exc:
            logger.error("Operations query failed (%s)", type(exc).__name__)
            return JsonResponse({"detail": "运维服务暂时无法完成请求，请稍后重试。"}, status=503)
    return guarded


def json_body(request, limit=16384):
    if request.content_type != "application/json":
        raise AnalysisError("请使用 JSON 查询参数。", 415)
    if len(request.body) > limit:
        raise AnalysisError("查询条件过长。", 413)
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise AnalysisError("查询参数格式无效。") from exc
    if not isinstance(data, dict):
        raise AnalysisError("查询参数必须是对象。")
    return data


@require_GET
@login_required
@never_cache
@ensure_csrf_cookie
def index(request):
    if not can_query(request.user):
        return render(request, "operations_workbench/access.html", {"operations_workspace": True}, status=403)
    assets = Path(settings.BASE_DIR) / "static" / "operations_workbench"
    digest = hashlib.sha256()
    for asset in sorted(assets.glob("**/*")):
        if asset.is_file():
            digest.update(asset.read_bytes())
    return render(request, "operations_workbench/index.html", {"operations_workspace": True,
                  "asset_version": digest.hexdigest()[:12]})


@require_GET
@api
def catalog(request):
    return JsonResponse(sources.catalog())


@require_POST
@api
def query(request):
    data = json_body(request)
    action = data.get("action")
    params = data.get("params", {})
    if not isinstance(action, str) or not isinstance(params, dict):
        raise AnalysisError("请选择操作，并填写有效的查询条件。")
    result = sources.run_action(action, params)
    return JsonResponse({"action": action, "source": action.partition(".")[0], "data": result})


@require_POST
@api
def ask(request):
    return JsonResponse(ai.ask(json_body(request, limit=65536)))


@require_GET
@api
def pdf(request, kind, code):
    content = sources.render_pdf(kind, code, request.GET.get("lang", "CN"))
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="operations-report.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
