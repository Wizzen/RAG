"""accounts views: 注册（登录/登出用 Django 内建）"""
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render

from .models import DEPARTMENT_GENERAL, UserProfile


def _existing_departments() -> list[str]:
    """已有的部门名（去重），供注册页 datalist 提示，避免自由填写拼写漂移。"""
    return list(
        UserProfile.objects.exclude(department="")
        .values_list("department", flat=True).distinct().order_by("department")
    )


def register_view(request):
    """用户注册（可填部门；默认「通用」）。"""
    if request.user.is_authenticated:
        return redirect("/")
    department = ""
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        department = (request.POST.get("department") or "").strip()
        if form.is_valid():
            user = form.save()
            UserProfile.objects.update_or_create(
                user=user, defaults={"department": department or DEPARTMENT_GENERAL},
            )
            login(request, user)
            return redirect("/")
    else:
        form = UserCreationForm()
    return render(request, "accounts/register.html", {
        "form": form,
        "department": department,
        "departments": _existing_departments(),
    })
