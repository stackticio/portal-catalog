from django.db import connection
from django.http import JsonResponse
from rest_framework import viewsets

from catalog.models import Listing
from catalog.serializers import ListingSerializer


def healthz(_request):
    """Liveness/readiness probe target (probe_path=/health). Checks DB connectivity."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception as exc:  # noqa: BLE001 - report any DB failure to the probe
        return JsonResponse({"status": "degraded", "error": str(exc)}, status=503)


class ListingViewSet(viewsets.ModelViewSet):
    queryset = Listing.objects.all()
    serializer_class = ListingSerializer
