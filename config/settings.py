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
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "catalog",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
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

CORS_ALLOW_ALL_ORIGINS = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"]}
