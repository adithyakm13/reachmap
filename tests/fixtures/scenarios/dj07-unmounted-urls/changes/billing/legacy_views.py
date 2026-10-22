from django.http import HttpResponse


def index(request):
    return HttpResponse("legacy")


def export(request):
    return HttpResponse("exported")
