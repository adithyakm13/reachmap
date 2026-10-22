from django.urls import path

from . import views

app_name = "cases"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:case_id>/", views.detail, name="detail"),
]
