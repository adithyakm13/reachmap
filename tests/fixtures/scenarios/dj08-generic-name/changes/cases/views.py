from django.http import JsonResponse


def index(request):
    return JsonResponse({"app": "cases", "open": 0})


def detail(request, case_id):
    return JsonResponse({"id": case_id})
