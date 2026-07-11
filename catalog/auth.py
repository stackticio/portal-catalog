"""Keycloak OIDC bearer-token authentication for DRF.

Access tokens are validated OFFLINE against Keycloak's JWKS (RS256): signature +
issuer + expiry. PyJWKClient caches the signing keys, so there is no per-request
round-trip to Keycloak. Anonymous requests return None so browse endpoints stay
public (via IsAuthenticatedOrReadOnly); writes (create listing, place bid) require a
valid token and take the caller's identity from the token — never the request body.

Config comes from env, which Stacktic renders from the catalog-svc `configmap`
attribute into the pod Secret (cloud.env):
  OIDC_ISSUER    e.g. https://keycloak.vibe.source-lab.io/realms/Stacktic
  OIDC_JWKS_URL  default <issuer>/protocol/openid-connect/certs
  OIDC_AUDIENCE  optional; when unset, audience is not enforced (Keycloak's default
                 aud is "account" and the client is identified by `azp`)
When OIDC_ISSUER is unset (local dev) this authenticator disables itself.
"""
import os

import jwt
from jwt import PyJWKClient
from rest_framework import authentication, exceptions

OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "").rstrip("/")
OIDC_JWKS_URL = os.environ.get("OIDC_JWKS_URL") or (
    f"{OIDC_ISSUER}/protocol/openid-connect/certs" if OIDC_ISSUER else ""
)
OIDC_AUDIENCE = os.environ.get("OIDC_AUDIENCE", "")

# One shared JWKS client (thread-safe, caches keys) or None when OIDC is off.
_jwks_client = PyJWKClient(OIDC_JWKS_URL) if OIDC_JWKS_URL else None


class TokenUser:
    """Minimal authenticated principal built from verified JWT claims (no DB row)."""

    is_authenticated = True
    is_anonymous = False

    def __init__(self, claims):
        self.claims = claims
        self.sub = claims.get("sub")
        self.username = claims.get("preferred_username") or self.sub or "unknown"
        self.email = claims.get("email", "")
        self.roles = list(claims.get("realm_access", {}).get("roles", []))

    def has_role(self, role):
        return role in self.roles

    def __str__(self):
        return self.username


class KeycloakJWTAuthentication(authentication.BaseAuthentication):
    """Validate `Authorization: Bearer <jwt>` against Keycloak's JWKS."""

    keyword = "Bearer"

    def authenticate(self, request):
        if _jwks_client is None:
            return None  # OIDC not configured (local dev): no auth layer
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith(self.keyword + " "):
            return None  # anonymous -> IsAuthenticatedOrReadOnly gates unsafe methods
        token = header[len(self.keyword) + 1 :].strip()
        try:
            signing_key = _jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=OIDC_ISSUER,
                audience=OIDC_AUDIENCE or None,
                options={"verify_aud": bool(OIDC_AUDIENCE), "require": ["exp", "iss"]},
            )
        except Exception as exc:  # noqa: BLE001 - any decode/verify failure -> 401
            raise exceptions.AuthenticationFailed(f"invalid token: {exc}")
        return (TokenUser(claims), token)

    def authenticate_header(self, request):
        return self.keyword
