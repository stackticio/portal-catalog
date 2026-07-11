# Feature Specification: Marketplace Catalog (browse & search)

**Feature Branch**: `001-marketplace-catalog`
**Created**: 2026-07-06
**Status**: Draft — documents shipped behaviour, hardens the read API
**Input**: "Spec all related to the app" → the buyer-facing catalog: list, filter, and search items.
**Component**: `catalog-svc` (Django) on the Stacktic vibe stack.

## Stack Context (from Stacktic metadata — NOT assumed)

These facts come from the stack repo (`stack-metadata.yaml` + generated `k8s/deploy/base/catalog-svc/config|secret/cloud.env`), the platform source of truth. Every requirement below is bound to them.

| Fact | Value | Source (generated) |
|------|-------|--------------------|
| Framework / port | `django`, container `:8000`, Service `:80`, `SERVICE_PORT=8000` | `graph(contract)` + `config/cloud.env` |
| Datastore | `DATABASE_URL=postgresql://catalog:…@cluster-cnpg-rw.cnpg.svc.cluster.local:5432/catalog` | link `external_source_code-db` → `catalog-db` (cnpg) |
| Image store | `S3_ENDPOINT_URL`, `S3_BUCKET=catalog-images`, `AWS_ACCESS_KEY_ID/SECRET`, `AWS_DEFAULT_REGION` | link `external_source_code-swbucket` → `catalog-images` (seaweedfs) |
| Exposure | inbound `apisix-external_source_code` → route on `apisix` (`api.vibe.source-lab.io`) | link `apisix-external_source_code` |

> Metadata correction the platform caught: `graph(contract)` reported the bucket env as `SW_*`, but the **generated `secret/cloud.env` uses `S3_*`/`AWS_*`** — code follows the generated file (Constitution Principle II). `settings.py` already reads `S3_*`. No code may hardcode any endpoint above.

## Current Implementation State (grounded)

- ✅ `Listing`, `Category` models (`catalog/models.py`).
- ✅ `GET /api/catalog/listings/` with `?category=<slug>` filter (`ListingViewSet.get_queryset`).
- ✅ `GET /api/catalog/categories/` with per-category `count` (`CategoryViewSet` + `CategorySerializer`).
- ✅ Image URL served as a **presigned** GET from the bucket (`storage.presigned_image_url`).
- ⚠️ **Gap**: no server-side text search; no pagination; no ordering choice (frontend filters client-side in `App.jsx`).

## User Scenarios & Testing

### User Story 1 — Browse all listings (Priority: P1)
A buyer opens the marketplace and sees every active listing, newest first, each with title, price, condition, category, and a photo.

**Why this priority**: The core marketplace surface; nothing else has value without it.
**Independent Test**: `GET /api/catalog/listings/` returns 200 with a listing array; each item carries `title, price, condition_label, category, image_url, highest_bid`.

**Acceptance Scenarios**:
1. **Given** N listings in `catalog-db`, **When** `GET /listings/`, **Then** all N are returned ordered by `-created_at`.
2. **Given** a listing with an `image_key` stored in `catalog-images`, **When** it is serialized, **Then** `image_url` is a presigned S3 GET URL pointing at `S3_BUCKET`.
3. **Given** a listing with no image, **When** serialized, **Then** `image_url` is `""` (client falls back to a placeholder).

### User Story 2 — Filter by category (Priority: P2)
**Acceptance Scenarios**:
1. **Given** categories with listings, **When** `GET /listings/?category=electronics`, **Then** only listings whose category slug is `electronics` return.
2. **Given** `GET /categories/`, **Then** each category returns `count` = number of its listings.

### User Story 3 — Server-side search (Priority: P3, GAP)
A buyer types a query and gets matching listings from the server (not just the current page filtered in the browser).

**Acceptance Scenarios**:
1. **Given** `GET /listings/?q=iphone`, **Then** listings whose `title`, `seller`, or `category name` match (case-insensitive) return.
2. **Given** many listings, **When** `GET /listings/?page=2`, **Then** results are paginated (page size documented in the response).

### Edge Cases
- Unknown `?category=` slug → empty list, 200 (not 404).
- Bucket unreachable → serialization must not 500; `image_url` degrades to `""`.
- `DATABASE_URL` unset (local dev) → falls back to sqlite per `settings.py` (must still serve).

## Functional Requirements
- **FR-1**: The service MUST read listings/categories only from `DATABASE_URL` (never a hardcoded DSN).
- **FR-2**: Image URLs MUST be generated against `S3_BUCKET` via the `S3_*` env credentials; image bytes are never stored in Postgres.
- **FR-3**: `GET /listings/` MUST default-order by `-created_at` and support `?category=<slug>`.
- **FR-4** (gap): add `?q=` server-side search over title/seller/category and pagination.
- **FR-5**: All endpoints MUST be reachable only through the `apisix` route (no direct exposure).

## Success Criteria (verify against the live stack)
- `stack_test(predefined)` for catalog-svc passes (DB reachable, route up).
- `/api/catalog/listings/` returns 200 through `api.vibe.source-lab.io`.
- Grep proves no hardcoded Postgres/S3 endpoint in `catalog/`; every connection reads the env names in Stack Context.
