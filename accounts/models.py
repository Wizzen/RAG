# accounts models：在 Django auth User 之外扩展 UserProfile（部门等字段）。
from __future__ import annotations

from django.conf import settings
from django.db import models

from kb.models import DEPARTMENT_GENERAL


class UserProfile(models.Model):
    """用户的扩展属性。OneToOne 挂在 auth User 上，避免替换自定义 User 模型。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile",
        verbose_name="用户",
    )
    department = models.CharField(
        "部门", max_length=50, default=DEPARTMENT_GENERAL,
        help_text="「通用」可见所有标为通用的知识库；其它值额外可见同部门知识库。",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "用户资料"
        verbose_name_plural = "用户资料"

    def __str__(self):
        return f"{self.user.username}（{self.department}）"


def user_department(user) -> str:
    """取用户部门；无 Profile（老用户/未落库）时惰性补建并返回「通用」。"""
    if user is None or not getattr(user, "is_authenticated", False):
        return DEPARTMENT_GENERAL
    profile = getattr(user, "profile", None)
    if profile is None:
        from .models import UserProfile  # noqa: PLC0415  局部 import 防御
        profile = UserProfile.objects.create(user=user, department=DEPARTMENT_GENERAL)
    return profile.department or DEPARTMENT_GENERAL
