from django.urls import path
from rest_framework.routers import DefaultRouter

from catalog.views import (
    CategoryViewSet,
    ListingViewSet,
    OrderViewSet,
    auth_callback,
    auth_config,
    auth_login,
    auth_register,
    bids_stream,
    kafka_health,
    listing_bids,
    listing_search,
    me_bids,
    me_listings,
    me_profile,
    me_watchlist,
)

router = DefaultRouter()
router.register("listings", ListingViewSet, basename="listing")
router.register("categories", CategoryViewSet, basename="category")
router.register("orders", OrderViewSet, basename="order")

urlpatterns = [
    # Keycloak hosted-login (Authorization-Code redirect) flow.
    path("auth/config/", auth_config, name="auth-config"),
    path("auth/callback/", auth_callback, name="auth-callback"),
    path("auth/register/", auth_register, name="auth-register"),
    # Alternate direct-grant proxy (scripted clients).
    path("auth/login/", auth_login, name="auth-login"),
    # Native mTLS Kafka connectivity check (connect + metadata + produce).
    path("kafka/health/", kafka_health, name="kafka-health"),
    # Real-time bidding: SSE stream of live bids (optionally ?listing=<id>).
    path("bids/stream/", bids_stream, name="bids-stream"),
    # Bid history for one listing.
    path("listings/<int:pk>/bids/", listing_bids, name="listing-bids"),
    # Full-text catalog search & discovery (OpenSearch, DB fallback).
    path("search/", listing_search, name="listing-search"),
    # "My Account" (eBay-style): profile/settings, my listings, my bids, watchlist.
    path("me/", me_profile, name="me-profile"),
    path("me/listings/", me_listings, name="me-listings"),
    path("me/bids/", me_bids, name="me-bids"),
    path("me/watchlist/", me_watchlist, name="me-watchlist"),
    *router.urls,
]
