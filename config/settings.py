"""
Django settings for the portal catalog service.

All infrastructure connections are read from environment variables that Stacktic
auto-generates from links (framework_type=django):
  - DATABASE_URL              (external_source_code-db   -> cnpg catalog_db)
  - REDIS_URL                 (external_source_code-valkey)
  - S3_ENDPOINT_URL, S3_BUCKET, AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION  (external_source_code-swbucket)
Local dev falls back to sqlite + locmem cache so the app runs with no infra.
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
# When hosts are restricted (prod), still allow the kubelet HTTP probe, which sends
# Host: <pod-ip>. The pod's own hostname resolves to its IP via the /etc/hosts entry
# kubelet writes, so probes stay green without needing a "*" wildcard.
if "*" not in ALLOWED_HOSTS:
    import socket

    try:
        ALLOWED_HOSTS.append(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "DIRS": [], "OPTIONS": {}}]

# --- Database: DATABASE_URL from the cnpg `db` link, sqlite fallback for local dev ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# --- Cache: REDIS_URL from the valkey link, locmem fallback ---
REDIS_URL = os.environ.get("REDIS_URL")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Object storage (product images): S3_* from the swbucket link ---
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_BUCKET = os.environ.get("S3_BUCKET")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_DEFAULT_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# --- Real-time bidding: Kafka HTTP Bridge (set in the catalog-svc configmap) ---
# external_source_code has no native Kafka topic link, so Django produces/consumes
# over the Strimzi bridge REST API instead of the native protocol.
KAFKA_BRIDGE_URL = os.environ.get("KAFKA_BRIDGE_URL")
KAFKA_BIDS_TOPIC = os.environ.get("KAFKA_BIDS_TOPIC", "bids")

# --- CORS/CSRF: the SPA is served same-origin (APISIX routes /api/catalog to this
# service under the portal host), so no cross-origin access is needed in the cluster.
# Allow only origins explicitly listed via env (empty default = same-origin only). ---
CORS_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# --- Security headers. APISIX terminates TLS and forwards X-Forwarded-Proto. ---
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
# HSTS is opt-in (0 = off) so a TLS hiccup on a demo can't lock the domain; enable in prod.
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Keycloak OIDC (env from the catalog-svc `configmap` -> pod Secret) ---
# Bearer tokens are validated in catalog/auth.py. OIDC_TOKEN_URL is used by the
# server-side login proxy so the confidential client secret never reaches the SPA.
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "").rstrip("/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_TOKEN_URL = os.environ.get("OIDC_TOKEN_URL") or (
    f"{OIDC_ISSUER}/protocol/openid-connect/token" if OIDC_ISSUER else ""
)
OIDC_AUTHORIZATION_URL = os.environ.get("OIDC_AUTHORIZATION_URL") or (
    f"{OIDC_ISSUER}/protocol/openid-connect/auth" if OIDC_ISSUER else ""
)

# --- API authorization: browse is public; writes (create listing, place bid)
# require a valid Keycloak token. Falls back to open when OIDC is unset (local dev). ---
if OIDC_ISSUER:
    REST_FRAMEWORK = {
        "DEFAULT_AUTHENTICATION_CLASSES": ["catalog.auth.KeycloakJWTAuthentication"],
        "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    }
else:
    REST_FRAMEWORK = {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"]}
