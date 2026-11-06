"""Django admin 注册。"""
from django.contrib import admin

from .models import Document, KnowledgeBase, StructuredDataset, StructuredRecord


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_folder", "department", "parent", "doc_count", "chunk_count", "created_at")
    list_filter = ("is_folder", "department")
    search_fields = ("name", "slug", "department")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("is_folder", "department")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "kb", "status", "chunk_count", "created_at")
    list_filter = ("status", "kb")
    search_fields = ("original_name",)


@admin.register(StructuredDataset)
class StructuredDatasetAdmin(admin.ModelAdmin):
    list_display = ("source_name", "kind", "row_count", "active", "imported_by", "created_at")
    list_filter = ("kind", "active")
    search_fields = ("name", "source_name")


@admin.register(StructuredRecord)
class StructuredRecordAdmin(admin.ModelAdmin):
    list_display = ("drawing_no", "part_no", "part_name", "g_code", "scp_level", "dataset", "row_number")
    list_filter = ("dataset__kind", "dataset")
    search_fields = ("drawing_no", "part_no", "part_name", "g_code", "equipment", "apex_no")
