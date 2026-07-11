# Feature Specification: Listing Management & Photo Upload

**Feature Branch**: `002-listing-management`
**Created**: 2026-07-06
**Status**: Draft — create/edit exist; **photo upload is the real gap**
**Input**: "List an item" → a seller creates a listing with a photo that lands in the image bucket.
**Component**: `catalog-svc` (Django).

## Stack Context (from Stacktic metadata — NOT assumed)

| Fact | Value | Source |
|------|-------|--------|
| Image store | `S3_ENDPOINT_URL=http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333`, `S3_BUCKET=catalog-images`, `AWS_ACCESS_KEY_ID=CatalogImgKey1`, `AWS_SECRET_ACCESS_KEY=…`, `AWS_DEFAULT_REGION=us-east-1` | generated `secret/cloud.env` (link `external_source_code-swbucket`) |
| Datastore | `DATABASE_URL` → `catalog-db` (cnpg) | link `external_source_code-db` |
| S3 client | already built: `catalog/storage.py::s3_client()` (boto3, reads the `S3_*` env) | app repo |

> The link **already generated** the bucket + credentials. The gap is purely application code — so per Constitution Principle I this is **code work, not a stack change**. No new link, no manifest.

## Current Implementation State (grounded)

- ✅ `POST /api/catalog/listings/` creates a listing (`ListingViewSet` = DRF `ModelViewSet`).
- ✅ `Listing.image_key` (S3 object key) + presigned **read** URL (`storage.presigned_image_url`).
- ✅ `boto3==1.34.122` in `requirements.txt`; `storage.s3_client()` builds a client from the swbucket env.
- ❌ **Gap 1**: no **upload** path — `storage.py` has `s3_client` + `presigned_image_url` (GET) but **no `put_object`**. Creating a listing only stores `image_key` as a string; no bytes ever reach `catalog-images`.
- ❌ **Gap 2**: create accepts any `image_key` from the client (unvalidated); no multipart image handling.

## User Scenarios & Testing

### User Story 1 — Create a listing with a photo (Priority: P1)
A seller submits title, price, category, condition, location, and a photo file. The photo is stored in `catalog-images`; the listing persists with the resulting `image_key`; buyers immediately see it with a working image.

**Why this priority**: This is the missing half of "list an item" — the whole point of the feature.
**Independent Test**: `POST /listings/` (multipart) with an image → 201; object exists in `S3_BUCKET`; `GET /listings/{id}/` returns a presigned `image_url` that resolves to the uploaded bytes.

**Acceptance Scenarios**:
1. **Given** a valid multipart create with an image, **When** `POST /listings/`, **Then** the bytes are `put_object`-ed into `S3_BUCKET` under a generated key (e.g. `listings/<uuid>.<ext>`) and the listing saves that `image_key`.
2. **Given** the created listing, **When** serialized, **Then** `image_url` is a presigned GET that returns HTTP 200 for the uploaded object.
3. **Given** a create with NO image, **When** `POST /listings/`, **Then** the listing saves with `image_key=""` and still returns 201.

### User Story 2 — Edit / remove own listing (Priority: P2)
**Acceptance Scenarios**:
1. **Given** an existing listing, **When** `PATCH /listings/{id}/` with a new photo, **Then** a new object is stored and `image_key` updated (old object cleanup is best-effort).
2. **Given** `DELETE /listings/{id}/`, **Then** the listing is removed (bucket object cleanup best-effort).

### User Story 3 — Reject bad uploads (Priority: P2)
**Acceptance Scenarios**:
1. **Given** a non-image file or a file over the size limit, **When** upload, **Then** 400 with a clear message; nothing is written to the bucket or DB.

### Edge Cases
- Bucket write fails mid-create → the listing MUST NOT be half-created (no DB row pointing at a missing object). Upload first, persist second, or wrap so a failed upload aborts the create.
- Duplicate/oversized images → enforce a max size and allowed content types.
- Local dev (`S3_ENDPOINT_URL` unset) → `s3_client()` returns `None`; create must degrade gracefully (store no `image_key`) rather than 500.

## Functional Requirements
- **FR-1**: Add `storage.put_image(file) -> image_key` using the existing `s3_client()` (`put_object` into `S3_BUCKET`). Reuse the module — do not create a second client.
- **FR-2**: `POST/PATCH /listings/` MUST accept `multipart/form-data` with an optional `image`; on presence, upload then set `image_key`.
- **FR-3**: The server MUST generate the object key (never trust a client-supplied `image_key`).
- **FR-4**: Validate content-type (image/*) and enforce a max upload size; reject with 400 otherwise.
- **FR-5**: All storage access MUST use the `S3_*`/`AWS_*` env only (Constitution Principle II).

## Success Criteria (verify against the live stack)
- After `POST` with an image: object listed in `catalog-images` (seaweedfs) and the presigned `image_url` returns 200.
- `stack_test(predefined)` still green (DB + bucket reachable).
- Grep proves the new upload path imports `catalog.storage` and references no literal bucket host/key.
