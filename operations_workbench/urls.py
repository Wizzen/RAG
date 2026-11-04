from django.urls import path
from . import views

app_name = "operations_workbench"
urlpatterns = [
    path("", views.index, name="index"),
    path("api/catalog/", views.catalog, name="catalog"),
    path("api/query/", views.query, name="query"),
    path("api/export/", views.export, name="export"),
    path("api/ask/", views.ask, name="ask"),
    path("api/pdf/<slug:kind>/<path:code>/", views.pdf, name="pdf"),
]
