from django.urls import path

from . import reports

urlpatterns = [
    path("monthly/", reports.monthly, name="monthly"),
]
