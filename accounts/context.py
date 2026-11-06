"""accounts context processors：向所有模板注入用户部门。"""
from __future__ import annotations


def user_department(request):
    """{{ user_department }} / {{ user_is_manager }} / {{ user_is_staff }} —— 登录用户的部门与权限级别。"""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {"user_department": "", "user_is_manager": False, "user_is_staff": False}
    try:
        from .models import user_department as _ud
        dept = _ud(user)
        is_staff = bool(user.is_staff or user.is_superuser)
        is_manager = is_staff
        if not is_manager:
            from .models import UserProfile, user_role
            is_manager = user_role(user) == UserProfile.Role.DEPT_ADMIN
        return {"user_department": dept, "user_is_manager": is_manager, "user_is_staff": is_staff}
    except Exception:
        return {"user_department": "", "user_is_manager": False, "user_is_staff": False}
