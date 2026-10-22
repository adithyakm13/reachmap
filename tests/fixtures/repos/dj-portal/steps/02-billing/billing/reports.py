from django.http import JsonResponse

from .services import format_reference


def index(request):
    return JsonResponse({"reports": []})


def monthly(request):
    return JsonResponse({"reference": format_reference("2024-01")})
