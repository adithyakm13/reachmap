from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Customer, Invoice
from .serializers import CustomerSerializer, InvoiceSerializer
from .services import outstanding_total


class InvoiceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        return Response({"id": pk, "void": True})

    @action(detail=False)
    def overdue(self, request):
        return Response({"outstanding": outstanding_total(self.get_queryset())})


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
