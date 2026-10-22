"""What Django and Django REST Framework say that the code of a project does not: the HTTP methods of the generic class views, the actions of
the viewset classes and mixins, the decorators that restrict the methods of a function view, and the names that wrap a view without changing
what it is. Tables of names, never imports: the adapter reads text and never imports the project or the framework.

A method is `GET`, `POST`, ... or `ANY` (the view answers every method, or the methods could not be told).
"""

from __future__ import annotations

__all__ = [
    "ACTIONS", "ANY", "DETAIL_ROUTE", "HTTP_NAMES", "LIST_ROUTE", "METHOD_DECORATORS", "MIXIN_ACTIONS", "NEUTRAL_SUFFIX", "ROUTER_CLASSES",
    "STATIC_HELPERS", "VIEWSET_BASES", "VIEW_BASES",
]

ANY = "ANY"
HTTP_NAMES = ("get", "post", "put", "patch", "delete", "head", "options", "trace")

# The methods each generic class view answers (Django's `django.views.generic`, DRF's `generics` and `views`).
VIEW_BASES: dict[str, tuple[str, ...]] = {
    "View": (),
    "TemplateView": ("GET",),
    "RedirectView": (ANY,),
    "ListView": ("GET",),
    "DetailView": ("GET",),
    "FormView": ("GET", "POST", "PUT"),
    "CreateView": ("GET", "POST", "PUT"),
    "UpdateView": ("GET", "POST", "PUT"),
    "DeleteView": ("DELETE", "GET", "POST"),
    "ArchiveIndexView": ("GET",),
    "YearArchiveView": ("GET",),
    "MonthArchiveView": ("GET",),
    "WeekArchiveView": ("GET",),
    "DayArchiveView": ("GET",),
    "TodayArchiveView": ("GET",),
    "DateDetailView": ("GET",),
    "APIView": (),
    "GenericAPIView": (),
    "ListAPIView": ("GET",),
    "CreateAPIView": ("POST",),
    "RetrieveAPIView": ("GET",),
    "DestroyAPIView": ("DELETE",),
    "UpdateAPIView": ("PATCH", "PUT"),
    "ListCreateAPIView": ("GET", "POST"),
    "RetrieveUpdateAPIView": ("GET", "PATCH", "PUT"),
    "RetrieveDestroyAPIView": ("DELETE", "GET"),
    "RetrieveUpdateDestroyAPIView": ("DELETE", "GET", "PATCH", "PUT"),
}

# The actions a viewset class has of its own (`rest_framework.viewsets`) and what each mixin adds.
ACTIONS = ("list", "create", "retrieve", "update", "partial_update", "destroy")
VIEWSET_BASES: dict[str, tuple[str, ...]] = {
    "ViewSet": (),
    "GenericViewSet": (),
    "ViewSetMixin": (),
    "ModelViewSet": ACTIONS,
    "ReadOnlyModelViewSet": ("list", "retrieve"),
}
MIXIN_ACTIONS: dict[str, tuple[str, ...]] = {
    "ListModelMixin": ("list",),
    "CreateModelMixin": ("create",),
    "RetrieveModelMixin": ("retrieve",),
    "UpdateModelMixin": ("update", "partial_update"),
    "DestroyModelMixin": ("destroy",),
}
NEUTRAL_SUFFIX = "Mixin"  # any other mixin adds no method a route could be tied to
BASE_OBJECTS = frozenset({"object"})

# DRF's `SimpleRouter.routes`: which method answers which action on the list and on the detail route.
LIST_ROUTE = (("GET", "list"), ("POST", "create"))
DETAIL_ROUTE = (("GET", "retrieve"), ("PUT", "update"), ("PATCH", "partial_update"), ("DELETE", "destroy"))
ROUTER_CLASSES = {"DefaultRouter": True, "SimpleRouter": False}  # the class and whether it adds an API root view

# Decorators of a function view that restrict its methods (`django.views.decorators.http`, `rest_framework.decorators.api_view`).
METHOD_DECORATORS: dict[str, tuple[str, ...]] = {
    "require_GET": ("GET",),
    "require_POST": ("POST",),
    "require_safe": ("GET", "HEAD"),
}
# Calls that give routes of their own in a URLconf but none that a person invokes: serving static files.
STATIC_HELPERS = frozenset({"static", "staticfiles_urlpatterns"})
