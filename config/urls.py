from django.urls import include, path

from catalog.views import healthz

urlpatterns = [
    path("health", healthz),
    path("api/catalog/", include("catalog.urls")),
]
