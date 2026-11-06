"""Only the new vehicle-tracker routes are registered here."""
from django.urls import path
from django.views.generic import RedirectView

from . import equipment, views

app_name = "vehicle_tracker"

urlpatterns = [
    path("equipment-models/", equipment.library, name="equipment_library"),
    path("equipment-models/api/catalog/", equipment.catalog, name="equipment_catalog"),
    path("equipment-models/sdmt/", views.index, name="sdmt"),
    path("vehicle-tracker", views.index, name="index"),
    path("vehicle-tracker/", RedirectView.as_view(pattern_name="vehicle_tracker:index")),
    path("vehicle-tracker/api/qaqc/parts/detail", views.qaqc_detail, name="qaqc_detail"),
    path("vehicle-tracker/api/ai/analyze", views.ai_analyze, name="ai_analyze"),
    path("vehicle-tracker/api/drawings/<str:code>/documents", views.drawing_documents, name="drawing_documents"),
    path("vehicle-tracker/api/documents/<uuid:doc_id>/location", views.document_location, name="document_location"),
    path("vehicle-tracker/api/documents/<uuid:doc_id>/page.png", views.document_page, name="document_page"),
    path("vehicle-tracker/api/records/<str:kind>/<str:code>", views.inline_record, name="inline_record"),
    path("vehicle-tracker/external/<str:destination>", views.external, name="external"),
]
