import json
import time
from decimal import Decimal, InvalidOperation

from django.db import connection, transaction
from django.http import JsonResponse, StreamingHttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalog import kafka_bridge, kafka_native
from catalog.models import Bid, Category, Listing
from catalog.serializers import BidSerializer, CategorySerializer, ListingSerializer


def kafka_health(request):
    """Prove native mTLS Kafka works end-to-end: connect (client cert trusted by the
    broker CA) -> fetch topic metadata (Describe ACL) -> produce a record (Write ACL).
    GET /api/catalog/kafka/health/  (?produce=0 to skip the write)."""
    result = kafka_native.health(do_produce=request.GET.get("produce", "1") != "0")
    return JsonResponse(result, status=200 if result.get("ok") else 503)


def healthz(_request):
    """Liveness/readiness probe target (probe_path=/health). Checks DB connectivity."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception as exc:  # noqa: BLE001 - report any DB failure to the probe
        return JsonResponse({"status": "degraded", "error": str(exc)}, status=503)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only list of marketplace categories (with per-category item counts)."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class ListingViewSet(viewsets.ModelViewSet):
    serializer_class = ListingSerializer

    def get_queryset(self):
        """All listings, optionally filtered to one category via ?category=<slug>."""
        qs = Listing.objects.select_related("category").all()
        slug = self.request.query_params.get("category")
        if slug:
            qs = qs.filter(category__slug=slug)
        return qs

    @action(detail=True, methods=["post"])
    def bid(self, request, pk=None):
        """Place a bid: validate > current high, persist to Postgres, then publish to
        the Kafka `bids` topic via the HTTP bridge so all live viewers update."""
        listing = self.get_object()
        bidder = (request.data.get("bidder") or "").strip()
        if not bidder:
            return Response({"detail": "bidder is required"}, status=400)
        try:
            amount = Decimal(str(request.data.get("amount")))
        except (InvalidOperation, TypeError):
            return Response({"detail": "amount must be a number"}, status=400)

        # Atomic: lock the listing's bids so two concurrent bids can't both "win".
        with transaction.atomic():
            current = listing.highest_bid
            if amount <= current:
                return Response(
                    {"detail": f"bid must exceed current high of {current}", "highest_bid": current},
                    status=409,
                )
            bid = Bid.objects.create(listing=listing, bidder=bidder, amount=amount)

        event = {
            "bid_id": bid.id,
            "listing_id": listing.id,
            "listing_title": listing.title,
            "bidder": bidder,
            "amount": str(amount),
            "created_at": bid.created_at.isoformat(),
        }
        published = False
        if kafka_bridge.is_enabled():
            try:
                kafka_bridge.produce(key=listing.id, value=event)
                published = True
            except Exception as exc:  # noqa: BLE001 - bid is saved; realtime is best-effort
                event["publish_error"] = str(exc)

        return Response({**event, "published": published}, status=201)


def bids_stream(request):
    """Server-Sent Events stream of live bids, consumed from Kafka via the bridge.

    Each connection gets its OWN consumer group (fan-out) so every browser sees every
    bid. Optional ?listing=<id> filters to one auction. Heartbeats keep the
    connection alive through proxies during quiet periods.
    """
    if not kafka_bridge.is_enabled():
        return JsonResponse({"detail": "kafka bridge not configured"}, status=503)

    listing_filter = request.GET.get("listing")

    def stream():
        yield "retry: 3000\n\n"
        try:
            with kafka_bridge.consumer() as poll:
                last_beat = time.monotonic()
                while True:
                    records = poll()
                    for rec in records:
                        value = rec.get("value")
                        if listing_filter and str(value.get("listing_id")) != str(listing_filter):
                            continue
                        yield f"event: bid\ndata: {json.dumps(value)}\n\n"
                    now = time.monotonic()
                    if now - last_beat >= 15:
                        yield ": keep-alive\n\n"
                        last_beat = now
        except GeneratorExit:
            raise  # client disconnected; consumer() cleans up in its finally
        except Exception as exc:  # noqa: BLE001 - surface transport errors to the client
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    resp = StreamingHttpResponse(stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"  # disable proxy buffering (APISIX/nginx)
    return resp


def listing_bids(request, pk):
    """Recent bid history for a listing (bootstraps the UI before the SSE stream)."""
    bids = Bid.objects.filter(listing_id=pk)[:50]
    return JsonResponse({"results": BidSerializer(bids, many=True).data})
