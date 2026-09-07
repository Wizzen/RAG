"""kb app URL 配置。"""
from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

app_name = "kb"

urlpatterns = [
    # 管理页面（仅 staff）
    path("manage/", views.manage_list, name="manage_list"),
    path("manage/<slug:slug>/", views.manage_detail, name="manage_detail"),
    path("manage/<slug:slug>/delete/", views.manage_delete, name="manage_delete"),
    path("manage/<slug:slug>/rename/", views.kb_rename, name="kb_rename"),
    path("manage/<slug:slug>/doc/<uuid:doc_id>/desc/", views.doc_desc_update, name="doc_desc_update"),
    path("manage/<slug:slug>/doc/<uuid:doc_id>/delete/", views.doc_delete, name="doc_delete"),
    path("manage/<slug:slug>/status/", views.doc_status_api, name="doc_status"),

    # 文档查看（所有登录用户）
    path("doc/<uuid:doc_id>/html/", views.document_html, name="document_html"),
    path("doc/<uuid:doc_id>/slices/", views.document_slices, name="document_slices"),
    path("doc/<uuid:doc_id>/img/<str:name>", views.doc_image, name="doc_image"),

    # 站点配置（仅 staff）
    path("settings/", views.site_settings, name="settings"),
    path("settings/test/", views.settings_test, name="settings_test"),
    path("eval/", views.eval_panel, name="eval_panel"),

    # 会话历史
    path("conv/<str:thread_id>/messages/", views.conversation_messages, name="conv_messages"),
    path("conv/<str:thread_id>/delete/", views.conversation_delete, name="conv_delete"),

    # 综合搜索（结构化跨表关联 + 手册全文）
    path("search/", views.search_view, name="search"),

    # 旧图纸关联地址（跳转至综合搜索）
    path("assets/", views.asset_lookup, name="asset_lookup"),

    # 检查项提取
    path("inspection/", views.inspection_list, name="inspection"),

    # 问答页面（所有登录用户）
    path("ask/", views.ask, name="ask"),
    path("stream/", views.chat_stream, name="stream"),

    # 默认入口 → 问答页
    path("", views.index, name="index"),
]
