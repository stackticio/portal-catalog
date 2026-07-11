# Feature Specification: Real-time Bidding

**Feature Branch**: `003-realtime-bidding`
**Created**: 2026-07-06
**Status**: Draft — shipped; this spec pins the contract & verification
**Input**: "Place a bid" → durable bid + live fan-out to every viewer.
**Component**: `catalog-svc` (Django) + Kafka `bids` topic.

## Stack Context (from Stacktic metadata — NOT assumed)

| Fact | Value | Source |
|------|-------|--------|
| Bids topic (native) | `KAFKA_BOOTSTRAP_SERVERS=kafka-kafka-bootstrap.strimzi.svc.cluster.local:9093`, `KAFKA_TOPICS=bids`, `KAFKA_SECURITY_PROTOCOL=SSL` | generated `secret/cloud.env` |
| mTLS identity (mounted files) | `KAFKA_SSL_CA_LOCATION=/etc/kafka/certs/ca.crt`, `…CERTIFICATE_LOCATION=/etc/kafka/certs/user.crt`, `…KEY_LOCATION=/etc/kafka/certs/user.key` | `kafka-mtls-secret.yaml` + volumeMount (generated) |
| Bridge (HTTP) | `KAFKA_BRIDGE_URL=http://kafka-bridge-bridge-service.kafka.svc.cluster.local:8080`, `KAFKA_BIDS_TOPIC=bids` | catalog-svc `configmap` attribute |
| Datastore | `DATABASE_URL` → `catalog-db` | link `external_source_code-db` |

> Two Kafka paths are wired by the platform: **native mTLS** (certs mounted as files, for the health check / native client) and the **HTTP bridge** (for produce/consume from Django). The `bids` topic + the mTLS Secret + the volumeMount were all generated from the stack — the app only consumes the env names above.

## Current Implementation State (grounded)

- ✅ `POST /api/catalog/listings/{id}/bid/` — validates amount > current high, persists `Bid` **atomically** (`transaction.atomic`, row lock), then publishes to Kafka via `kafka_bridge.produce` (`views.ListingViewSet.bid`).
- ✅ `GET /api/catalog/bids/stream/` — SSE stream; each connection gets its own consumer group → every viewer sees every bid (`views.bids_stream`).
- ✅ `GET /api/catalog/listings/{id}/bids/` — recent bid history (bootstraps UI before SSE).
- ✅ `GET /api/catalog/kafka/health/` — native mTLS connect + metadata + produce check (`kafka_native.health`).
- ✅ Bid persisted even if publish fails (realtime is best-effort; `published` flag returned).

## User Scenarios & Testing

### User Story 1 — Place a winning bid (Priority: P1)
A signed-in buyer bids above the current high; the bid is saved and instantly shown to all viewers.

**Acceptance Scenarios**:
1. **Given** current high H, **When** `POST /listings/{id}/bid/` with `amount > H`, **Then** 201, a `Bid` row is created, and an event is published to `bids`.
2. **Given** `amount <= H`, **When** POST, **Then** 409 with `highest_bid` and no row created.
3. **Given** two concurrent bids above H, **When** both POST, **Then** the `transaction.atomic` lock ensures only one wins; the other gets 409.
4. **Given** missing `bidder` or non-numeric `amount`, **Then** 400.

### User Story 2 — Live updates for every viewer (Priority: P1)
**Acceptance Scenarios**:
1. **Given** an open `GET /bids/stream/` connection, **When** any bid is placed, **Then** a `bid` SSE event with `{listing_id, bidder, amount, listing_title}` is pushed to every connection.
2. **Given** `?listing=<id>`, **Then** only that auction's events are delivered.
3. **Given** a quiet period, **Then** keep-alive heartbeats keep the connection open through APISIX.

### User Story 3 — Bid history (Priority: P2)
1. **Given** a listing, **When** `GET /listings/{id}/bids/`, **Then** up to 50 recent bids return newest-first.

### Edge Cases
- Bridge down at bid time → bid still persists (201), `published:false`, `publish_error` surfaced.
- SSE consumer error → stream yields an `error` event, not a 500.
- Bridge not configured → `/bids/stream/` returns 503 (documented, not a crash).

## Functional Requirements
- **FR-1**: Bids MUST be durable in `catalog-db` **before** publishing (persistence is source of truth; Kafka is fan-out).
- **FR-2**: Amount MUST strictly exceed the current highest bid; enforce under a row lock.
- **FR-3**: Produce/consume MUST use `KAFKA_BRIDGE_URL` + `KAFKA_BIDS_TOPIC` (bridge) — never a hardcoded broker address.
- **FR-4**: The native health check MUST authenticate via the mounted mTLS cert paths (`KAFKA_SSL_*_LOCATION`).
- **FR-5**: SSE MUST set `X-Accel-Buffering: no` so APISIX/nginx does not buffer the stream.

## Success Criteria (verify against the live stack)
- `GET /kafka/health/?produce=1` → `ok:true` (native mTLS connect + write proven).
- Two browser tabs on `/bids/stream/`: a bid in one appears in both < 1s.
- `stack_test(predefined)` green.
- Grep proves no literal broker host in `catalog/` outside the env-driven config.
