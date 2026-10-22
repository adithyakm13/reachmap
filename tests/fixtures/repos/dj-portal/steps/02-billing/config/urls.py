from django.contrib import admin
from django.urls import include, path, re_path

from billing import views as billing_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("billing/", include("billing.urls")),
    re_path(r"^legacy/(?P<year>[0-9]{4})/$", billing_views.legacy_year),
]
