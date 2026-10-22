from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("invoices/", views.invoice_list, name="invoice-list"
]
