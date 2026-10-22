from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("invoices/", views.invoice_list, name="invoice-list"),
    path("invoices/<int:invoice_id>/", views.invoice_detail, name="invoice-detail"),
    path("invoices/<int:invoice_id>/void/", views.VoidInvoiceView.as_view(), name="invoice-void"),
    path("invoices/export/", views.invoice_export, name="invoice-export"),
    path("reports/", include("billing.report_urls")),
    # path("legacy/", include("billing.legacy_urls")),
]
