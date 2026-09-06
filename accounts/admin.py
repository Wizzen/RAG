"""accounts admin：UserProfile（部门）管理。"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "用户资料（部门/账号级别）"


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "department", "role", "updated_at")
    list_filter = ("department", "role")
    search_fields = ("user__username", "department")
    list_editable = ("department", "role")


# 在 Django 自带 UserAdmin 里内联显示 Profile（改用户部门更顺手）
UserAdmin.inlines = list(UserAdmin.inlines) + [UserProfileInline]
