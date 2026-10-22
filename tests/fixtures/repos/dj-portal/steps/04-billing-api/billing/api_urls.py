from django.urls import include, path
from rest_framework import routers

from . import api

router = routers.DefaultRouter()
router.register(r"invoices", api.InvoiceViewSet, basename="invoice")
router.register(r"customers", api.CustomerViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
