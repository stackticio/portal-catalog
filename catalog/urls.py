from django.urls import path
from rest_framework.routers import DefaultRouter

from catalog.views import (
    CategoryViewSet,
    ListingViewSet,
    auth_callback,
    auth_config,
    auth_login,
    bids_stream,
    kafka_health,
    listing_bids,
)

router = DefaultRouter()
router.register("listings", ListingViewSet, basename="listing")
router.register("categories", CategoryViewSet, basename="category")

urlpatterns = [
    # Keycloak hosted-login (Authorization-Code redirect) flow.
    path("auth/config/", auth_config, name="auth-config"),
    path("auth/callback/", auth_callback, name="auth-callback"),
    # Alternate direct-grant proxy (scripted clients).
    path("auth/login/", auth_login, name="auth-login"),
    # Native mTLS Kafka connectivity check (connect + metadata + produce).
    path("kafka/health/", kafka_health, name="kafka-health"),
    # Real-time bidding: SSE stream of live bids (optionally ?listing=<id>).
    path("bids/stream/", bids_stream, name="bids-stream"),
    # Bid history for one listing.
    path("listings/<int:pk>/bids/", listing_bids, name="listing-bids"),
    *router.urls,
]
