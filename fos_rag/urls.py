"""fos_rag URL Configuration."""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from kb import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("kb/", include("kb.urls")),
    path("dashboard/", include("dashboard.urls")),
    # 首页：简单导航
    path("", views.home, name="home"),
    # 设计原型（可移除）
    path("prototype/", TemplateView.as_view(template_name="prototype.html"), name="prototype"),
]
