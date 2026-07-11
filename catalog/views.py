import json
import time
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse, StreamingHttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
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

    def perform_create(self, serializer):
        # Seller is the authenticated token subject, never client-supplied.
        serializer.save(seller=self.request.user.username)

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
