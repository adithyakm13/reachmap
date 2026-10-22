from django.http import JsonResponse
from django.views import View
from django.views.decorators.http import require_http_methods

from .services import outstanding_total


def index(request):
    return JsonResponse({"app": "billing"})


def invoice_list(request):
    return JsonResponse({"outstanding": outstanding_total([])})


@require_http_methods(["GET", "PUT"])
def invoice_detail(request, invoice_id):
    return JsonResponse({"id": invoice_id})


class VoidInvoiceView(View):
    def post(self, request, invoice_id):
        return JsonResponse({"id": invoice_id, "void": True})


def legacy_year(request, year):
    return JsonResponse({"year": year})


def invoice_export(request):
    return JsonResponse({"export": True})
