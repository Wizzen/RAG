"""accounts context processors：向所有模板注入用户部门。"""
from __future__ import annotations


def user_department(request):
    """{{ user_department }} —— 当前登录用户的部门（未登录/异常时为空串）。"""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {"user_department": ""}
    try:
        from .models import user_department as _ud
        return {"user_department": _ud(user)}
    except Exception:
        return {"user_department": ""}
