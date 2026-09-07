"""accounts app: 登录/注册/登出/用户部门管理 URL"""
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register_view, name="register"),
    path("password/change/", views.password_change, name="password_change"),
    path("users/", views.user_list, name="user_list"),
]
