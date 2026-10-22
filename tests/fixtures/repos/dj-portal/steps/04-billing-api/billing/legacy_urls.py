from django.urls import path

from . import legacy_views

urlpatterns = [
    path("export/", legacy_views.export, name="legacy-export"),
]
