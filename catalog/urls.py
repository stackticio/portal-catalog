from django.urls import path
from rest_framework.routers import DefaultRouter

from catalog.views import ListingViewSet, bids_stream, kafka_health, listing_bids

router = DefaultRouter()
router.register("listings", ListingViewSet, basename="listing")

urlpatterns = [
    # Native mTLS Kafka connectivity check (connect + metadata + produce).
    path("kafka/health/", kafka_health, name="kafka-health"),
    # Real-time bidding: SSE stream of live bids (optionally ?listing=<id>).
    path("bids/stream/", bids_stream, name="bids-stream"),
    # Bid history for one listing.
    path("listings/<int:pk>/bids/", listing_bids, name="listing-bids"),
    *router.urls,
]
