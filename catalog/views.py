import json
import time
import uuid
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, F, Max, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse, StreamingHttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from catalog import kafka_bridge, kafka_native, opensearch_client
from catalog.models import Bid, Category, Listing, Order, OrderItem, Profile, Watchlist
from catalog.serializers import (
    BidSerializer,
    CategorySerializer,
    ListingSerializer,
    MyBidSerializer,
    MyListingSerializer,
    OrderSerializer,
    ProfileSerializer,
    WatchlistSerializer,
)


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


class ListingPagination(PageNumberPagination):
    """Bounded catalog pages. `size` matches the /search/ param so clients use one name."""

    page_size = 24
    page_size_query_param = "size"
    max_page_size = 100


class ListingViewSet(viewsets.ModelViewSet):
    serializer_class = ListingSerializer
    pagination_class = ListingPagination

    def get_queryset(self):
        """All listings, optionally filtered to one category via ?category=<slug>.
        current_high is annotated in the SAME query (Coalesce to price when no bids)
        so serializing a page never runs one bids query per row."""
        qs = (
            Listing.objects.select_related("category")
            .annotate(current_high=Coalesce(Max("bids__amount"), F("price")))
            # The aggregation drops Meta.ordering; explicit order (with an id
            # tiebreak) keeps page boundaries deterministic.
            .order_by("-created_at", "-id")
        )
        slug = self.request.query_params.get("category")
        if slug:
            qs = qs.filter(category__slug=slug)
        return qs

    def perform_create(self, serializer):
        # Seller is the authenticated token subject, never client-supplied.
        serializer.save(seller=self.request.user.username)

    def perform_update(self, serializer):
        # Object-level ownership: only the seller may edit their listing. (DRF's
        # IsAuthenticatedOrReadOnly gates anonymous writes but not WHOSE object.)
        if serializer.instance.seller != self.request.user.username:
            raise PermissionDenied("only the seller can modify this listing")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.seller != self.request.user.username:
            raise PermissionDenied("only the seller can delete this listing")
        instance.delete()

    @action(detail=True, methods=["post"])
    def bid(self, request, pk=None):
        """Place a bid: the bidder identity comes from the verified token (never the
        request body), the amount must beat the current high, and the write happens
        under a listing row lock; then publish to the Kafka `bids` topic so all live
        viewers update."""
        listing = self.get_object()
        # IsAuthenticatedOrReadOnly has already rejected anonymous POSTs.
        bidder = request.user.username
        try:
            amount = Decimal(str(request.data.get("amount")))
        except (InvalidOperation, TypeError):
            return Response({"detail": "amount must be a number"}, status=400)

        # Serialize concurrent bids on the SAME listing by taking a row lock on the
        # listing (the mutex) BEFORE reading the current high. Without this the
        # previous code held an atomic() block but locked nothing, so two bids could
        # both pass the check below and both "win". select_for_update needs an open
        # transaction; the second bidder blocks here until the first commits, then
        # re-reads the now-higher current high. (No-op on the sqlite dev fallback;
        # enforced on Postgres in the cluster.)
        with transaction.atomic():
            listing = Listing.objects.select_for_update().get(pk=listing.pk)
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

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None):
        """Buy-now purchase. Under a listing ROW LOCK: verify it's a `buy_now` still
        `active` and not the buyer's own, create a PAID order (simulated payment) and
        mark the listing sold — all atomically, so two concurrent buyers can't both
        win. Auth required (IsAuthenticatedOrReadOnly rejects anonymous POSTs)."""
        buyer = request.user.username
        with transaction.atomic():
            listing = Listing.objects.select_for_update().get(pk=self.get_object().pk)
            if listing.sell_type != "buy_now":
                return Response({"detail": "not available for direct purchase"}, status=409)
            if listing.status != "active":
                return Response({"detail": "already sold"}, status=409)
            if listing.seller == buyer:
                return Response({"detail": "cannot buy your own listing"}, status=409)
            order = Order.objects.create(
                buyer=buyer,
                seller=listing.seller,
                total=listing.price,
                status="paid",
                payment_ref=f"SIMULATED-{uuid.uuid4()}",
            )
            OrderItem.objects.create(
                order=order, listing=listing, title=listing.title, price=listing.price
            )
            listing.status = "sold"
            listing.save(update_fields=["status"])
        _publish_order_event("order.paid", order)
        return Response(OrderSerializer(order).data, status=201)

    @action(detail=True, methods=["post", "delete"], permission_classes=[IsAuthenticated])
    def watch(self, request, pk=None):
        """POST /listings/{id}/watch/ — follow a listing (idempotent). DELETE — unfollow.
        Keyed by the token subject so it survives username changes."""
        listing = self.get_object()
        sub = request.user.sub
        if request.method == "DELETE":
            Watchlist.objects.filter(user_sub=sub, listing=listing).delete()
            return Response(status=204)
        Watchlist.objects.get_or_create(user_sub=sub, listing=listing)
        return Response({"watching": True, "listing": listing.id}, status=201)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def end(self, request, pk=None):
        """POST /listings/{id}/end/ — the owner ends their own active listing. A
        non-owner gets 404 (never learns the listing exists via this action)."""
        listing = self.get_object()
        if listing.seller != request.user.username:
            return Response({"detail": "not found"}, status=404)
        if listing.status != "active":
            return Response({"detail": f"listing is already {listing.status}"}, status=409)
        listing.status = "ended"
        listing.save(update_fields=["status"])
        return Response(ListingSerializer(listing).data)


def _publish_order_event(event_type, order):
    """Best-effort publish of an order lifecycle event to the Kafka `orders` topic via
    the HTTP bridge. No-op until the topic is wired (settings.KAFKA_ORDERS_TOPIC empty)
    — the persisted Order is always the source of truth."""
    topic = getattr(settings, "KAFKA_ORDERS_TOPIC", "")
    bridge = getattr(settings, "KAFKA_BRIDGE_URL", "")
    if not (topic and bridge):
        return
    try:
        requests.post(
            f"{bridge}/topics/{topic}",
            headers={"Content-Type": "application/vnd.kafka.json.v2+json"},
            data=json.dumps(
                {
                    "records": [
                        {
                            "key": str(order.id),
                            "value": {
                                "event": event_type,
                                "order_id": order.id,
                                "buyer": order.buyer,
                                "seller": order.seller,
                                "total": str(order.total),
                                "status": order.status,
                            },
                        }
                    ]
                }
            ),
            timeout=5,
        )
    except requests.RequestException:
        pass  # realtime is best-effort; the order already persisted


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """Buyer/seller order views. Scoped so a caller only ever sees orders they bought
    or sold (retrieve 404s otherwise — no cross-tenant leakage). Orders are created via
    the listing `checkout` action, never by direct write."""

    serializer_class = OrderSerializer

    def get_queryset(self):
        me = self.request.user.username
        return Order.objects.filter(Q(buyer=me) | Q(seller=me)).prefetch_related("items")

    def list(self, request, *args, **kwargs):
        """GET /orders/ — things I BOUGHT."""
        qs = self.get_queryset().filter(buyer=request.user.username)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def sales(self, request):
        """GET /orders/sales/ — things I SOLD."""
        qs = self.get_queryset().filter(seller=request.user.username)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def ship(self, request, pk=None):
        """POST /orders/{id}/ship/ — seller-only, paid -> shipped."""
        order = self.get_object()
        if order.seller != request.user.username:
            return Response({"detail": "only the seller can ship this order"}, status=403)
        if order.status != "paid":
            return Response({"detail": f"cannot ship an order that is {order.status}"}, status=409)
        order.status = "shipped"
        order.save(update_fields=["status"])
        _publish_order_event("order.shipped", order)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """POST /orders/{id}/complete/ — buyer-only, shipped -> completed."""
        order = self.get_object()
        if order.buyer != request.user.username:
            return Response({"detail": "only the buyer can complete this order"}, status=403)
        if order.status != "shipped":
            return Response({"detail": f"cannot complete an order that is {order.status}"}, status=409)
        order.status = "completed"
        order.save(update_fields=["status"])
        _publish_order_event("order.completed", order)
        return Response(self.get_serializer(order).data)


def _get_or_create_profile(user):
    """Lazily create the caller's Profile from verified token claims (once per subject)."""
    profile, _ = Profile.objects.get_or_create(
        sub=user.sub,
        defaults={"username": user.username, "contact_email": getattr(user, "email", "") or ""},
    )
    return profile


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def me_profile(request):
    """GET /me/ — my account/settings (auto-created on first read).
    PATCH /me/ — update editable fields only (identity is token-derived, never from body)."""
    profile = _get_or_create_profile(request.user)
    if request.method == "PATCH":
        ser = ProfileSerializer(profile, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()  # read_only_fields (sub/username) are ignored even if sent
        return Response(ser.data)
    return Response(ProfileSerializer(profile).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_listings(request):
    """GET /me/listings/ — the items I'm selling, with bid_count + highest_bid.
    Optional ?status=active|sold|ended."""
    qs = (
        Listing.objects.filter(seller=request.user.username)
        .select_related("category")
        .annotate(bid_count=Count("bids"))
    )
    status_f = request.query_params.get("status")
    if status_f:
        qs = qs.filter(status=status_f)
    return Response(MyListingSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_bids(request):
    """GET /me/bids/ — one row per auction I've bid on, labelled winning/outbid/won/lost.
    Bounded queries: my per-listing max, then the listings annotated with current high."""
    me = request.user.username
    my_maxes = {
        row["listing"]: row["my_max"]
        for row in Bid.objects.filter(bidder=me).values("listing").annotate(my_max=Max("amount"))
    }
    if not my_maxes:
        return Response([])
    listings = (
        Listing.objects.filter(id__in=list(my_maxes))
        .select_related("category")
        .annotate(current_high=Max("bids__amount"))
    )
    rows = []
    for lst in listings:
        my_max = my_maxes[lst.id]
        current_high = lst.current_high if lst.current_high is not None else lst.price
        leading = my_max >= current_high
        if lst.status in ("ended", "sold"):
            state = "won" if leading else "lost"
        else:
            state = "winning" if leading else "outbid"
        rows.append({"listing": lst, "my_max": my_max, "current_high": current_high, "state": state})
    state_f = request.query_params.get("state")
    if state_f:
        rows = [r for r in rows if r["state"] == state_f]
    return Response(MyBidSerializer(rows, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_watchlist(request):
    """GET /me/watchlist/ — listings I'm watching, newest first, with live listing fields."""
    qs = (
        Watchlist.objects.filter(user_sub=request.user.sub)
        .select_related("listing", "listing__category")
    )
    return Response(WatchlistSerializer(qs, many=True).data)


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


def _search_price(value):
    """Parse an optional price filter; ignore anything non-numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _search_page(qp):
    """Clamp page/size query params to sane bounds."""
    try:
        page = max(1, int(qp.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = min(100, max(1, int(qp.get("size", 24))))
    except (TypeError, ValueError):
        size = 24
    return page, size


def _hydrate_listings(ids):
    """Load listings for the given ids, preserving the OpenSearch ranking order.
    Same current_high annotation as the list view: one query for the whole page."""
    if not ids:
        return []
    qs = (
        Listing.objects.filter(id__in=ids)
        .select_related("category")
        .annotate(current_high=Coalesce(Max("bids__amount"), F("price")))
    )
    by_id = {lst.id: lst for lst in qs}
    return [by_id[i] for i in ids if i in by_id]


def _db_search(f, page, size):
    """DB fallback so /search works with no OpenSearch (local dev, or the cluster down).
    Same filters as the OpenSearch path; ranking is newest-first (no relevance in SQL)."""
    qs = Listing.objects.select_related("category")
    if f["q"]:
        qs = qs.filter(Q(title__icontains=f["q"]) | Q(description__icontains=f["q"]))
    if f["category"]:
        qs = qs.filter(category__slug=f["category"])
    if f["condition"]:
        qs = qs.filter(condition=f["condition"])
    if f["sell_type"]:
        qs = qs.filter(sell_type=f["sell_type"])
    if f["status"] and f["status"] != "all":
        qs = qs.filter(status=f["status"])
    if f["min_price"] is not None:
        qs = qs.filter(price__gte=f["min_price"])
    if f["max_price"] is not None:
        qs = qs.filter(price__lte=f["max_price"])
    order = {"price_asc": "price", "price_desc": "-price"}.get(f["sort"], "-created_at")
    qs = qs.order_by(order)
    total = qs.count()
    start = (page - 1) * size
    return list(qs[start:start + size]), total


@api_view(["GET"])
@permission_classes([AllowAny])
def listing_search(request):
    """GET /api/catalog/search/ — full-text catalog search & discovery.

    Params: q, category(slug), condition, sell_type, status(active|sold|ended|all,
    default active), min_price, max_price, sort(relevance|newest|price_asc|price_desc),
    page, size. Ranking/filtering runs in OpenSearch; the matched listings are hydrated
    from the DB so the payload is identical to /listings/. If OpenSearch is unavailable
    the endpoint transparently falls back to a database search (backend="database")."""
    qp = request.query_params
    f = {
        "q": (qp.get("q") or "").strip(),
        "category": (qp.get("category") or "").strip(),
        "condition": (qp.get("condition") or "").strip(),
        "sell_type": (qp.get("sell_type") or "").strip(),
        "status": (qp.get("status") or "active").strip(),
        "sort": (qp.get("sort") or "relevance").strip(),
        "min_price": _search_price(qp.get("min_price")),
        "max_price": _search_price(qp.get("max_price")),
    }
    page, size = _search_page(qp)

    backend = "opensearch"
    if opensearch_client.is_enabled():
        try:
            hits = opensearch_client.search(page=page, size=size, **f)
            listings = _hydrate_listings(hits["ids"])
            total = hits["total"]
        except Exception:  # noqa: BLE001 - degrade to DB search, never 500 the UI
            backend = "database"
            listings, total = _db_search(f, page, size)
    else:
        backend = "database"
        listings, total = _db_search(f, page, size)

    return Response({
        "query": f["q"],
        "backend": backend,
        "total": total,
        "page": page,
        "size": size,
        "results": ListingSerializer(listings, many=True).data,
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def auth_config(request):
    """Public OIDC parameters the SPA needs to START the Keycloak hosted-login
    redirect (Authorization-Code flow). client_id and the authorize endpoint are
    public; the client secret is never sent — it's used only server-side in
    /auth/callback/."""
    if not settings.OIDC_AUTHORIZATION_URL:
        return Response({"detail": "authentication is not configured"}, status=503)
    return Response(
        {
            "authorization_endpoint": settings.OIDC_AUTHORIZATION_URL,
            "client_id": settings.OIDC_CLIENT_ID,
            "issuer": settings.OIDC_ISSUER,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def auth_callback(request):
    """Finish the Keycloak hosted-login redirect: exchange the authorization `code`
    for tokens using the confidential client secret (server-side only). The SPA then
    stores the access_token and sends it as `Authorization: Bearer` on writes."""
    if not settings.OIDC_TOKEN_URL:
        return Response({"detail": "authentication is not configured"}, status=503)
    code = request.data.get("code")
    redirect_uri = request.data.get("redirect_uri")
    if not code or not redirect_uri:
        return Response({"detail": "code and redirect_uri are required"}, status=400)
    try:
        resp = requests.post(
            settings.OIDC_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.OIDC_CLIENT_ID,
                "client_secret": settings.OIDC_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return Response({"detail": f"auth server unreachable: {exc}"}, status=502)
    if resp.status_code != 200:
        return Response({"detail": "code exchange failed"}, status=401)
    tok = resp.json()
    # Read the username from the returned token (server-to-server exchange, already
    # trusted); the API re-verifies the signature on every subsequent write.
    username = ""
    try:
        import jwt as _jwt

        claims = _jwt.decode(tok.get("access_token", ""), options={"verify_signature": False})
        username = claims.get("preferred_username", "")
    except Exception:  # noqa: BLE001 - username is best-effort decoration
        pass
    return Response(
        {
            "access_token": tok.get("access_token"),
            "refresh_token": tok.get("refresh_token"),
            "expires_in": tok.get("expires_in"),
            "username": username,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def auth_login(request):
    """Alternate server-side login proxy (direct-grant): username+password -> tokens,
    kept as a fallback for scripted/non-browser clients. Browser sign-in uses the
    hosted-redirect flow above (auth_config + auth_callback)."""
    if not settings.OIDC_TOKEN_URL:
        return Response({"detail": "authentication is not configured"}, status=503)
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    if not username or not password:
        return Response({"detail": "username and password are required"}, status=400)
    try:
        resp = requests.post(
            settings.OIDC_TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": settings.OIDC_CLIENT_ID,
                "client_secret": settings.OIDC_CLIENT_SECRET,
                "username": username,
                "password": password,
                "scope": "openid",
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return Response({"detail": f"auth server unreachable: {exc}"}, status=502)
    if resp.status_code != 200:
        return Response({"detail": "invalid credentials"}, status=401)
    tok = resp.json()
    return Response(
        {
            "access_token": tok.get("access_token"),
            "refresh_token": tok.get("refresh_token"),
            "expires_in": tok.get("expires_in"),
            "username": username,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def auth_register(request):
    """Self-service sign-up: create a Keycloak user via the Admin API, then auto-login.

    User creation uses the portal client's SERVICE ACCOUNT (client_credentials +
    manage-users role) against the INTERNAL Keycloak service — the public /admin route
    is blocked at the gateway. Auto-login uses the PUBLIC token endpoint so the issued
    token's `iss` matches what catalog/auth.py validates on subsequent writes.
    """
    if not (settings.OIDC_ADMIN_BASE and settings.OIDC_REALM):
        return Response({"detail": "registration is not configured"}, status=503)
    d = request.data
    username = (d.get("username") or "").strip()
    email = (d.get("email") or "").strip()
    password = d.get("password") or ""
    first = (d.get("firstName") or "").strip()
    last = (d.get("lastName") or "").strip()
    if not username or not email or not password:
        return Response({"detail": "username, email and password are required"}, status=400)

    base, realm = settings.OIDC_ADMIN_BASE, settings.OIDC_REALM

    # 1) service-account token (in-cluster)
    try:
        tr = requests.post(
            f"{base}/realms/{realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.OIDC_CLIENT_ID,
                "client_secret": settings.OIDC_CLIENT_SECRET,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return Response({"detail": f"auth server unreachable: {exc}"}, status=502)
    if tr.status_code != 200:
        return Response({"detail": "registration backend authentication failed"}, status=502)
    admin_token = tr.json().get("access_token")

    # 2) create the user
    try:
        cr = requests.post(
            f"{base}/admin/realms/{realm}/users",
            headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
            json={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": True,
                "firstName": first,
                "lastName": last,
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return Response({"detail": f"auth server unreachable: {exc}"}, status=502)
    if cr.status_code == 409:
        return Response({"detail": "that username or email is already taken"}, status=409)
    if cr.status_code not in (201, 204):
        return Response({"detail": "could not create the account"}, status=400)

    # 3) auto-login via the PUBLIC token endpoint (correct issuer for later validation)
    payload = {"username": username, "access_token": None, "refresh_token": None, "expires_in": None}
    if settings.OIDC_TOKEN_URL:
        try:
            lr = requests.post(
                settings.OIDC_TOKEN_URL,
                data={
                    "grant_type": "password",
                    "client_id": settings.OIDC_CLIENT_ID,
                    "client_secret": settings.OIDC_CLIENT_SECRET,
                    "username": username,
                    "password": password,
                    "scope": "openid",
                },
                timeout=10,
            )
            if lr.status_code == 200:
                tok = lr.json()
                payload["access_token"] = tok.get("access_token")
                payload["refresh_token"] = tok.get("refresh_token")
                payload["expires_in"] = tok.get("expires_in")
        except requests.RequestException:
            pass  # account exists; SPA falls back to the normal sign-in
    return Response(payload, status=201)
