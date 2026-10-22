from django.http import JsonResponse


def index(request):
    return JsonResponse({"app": "cases"})


def detail(request, case_id):
    return JsonResponse({"id": case_id})
