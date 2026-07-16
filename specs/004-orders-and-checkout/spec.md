# Feature Specification: Orders & Checkout

**Feature Branch**: `004-orders-and-checkout`
**Created**: 2026-07-11
**Status**: Draft
**Input**: "A won auction / buy-now becomes an Order. Checkout + simulated payment. The start of the money path."
**Component**: `catalog-svc` (Django). Paired frontend: `portal-frontend` (separate spec).

## Stack Context (from Stacktic metadata — NOT assumed)

| Fact | Value | Source |
|------|-------|--------|
| Datastore | `DATABASE_URL` → `catalog-db` (cnpg) | link `external_source_code-db` |
| Identity | Keycloak OIDC; buyer/seller taken from the verified token, never the body | `OIDC_ISSUER`, `catalog/auth.py` |
| Cache | `REDIS_URL` → valkey | link `external_source_code-valkey` |
| Event bus (existing) | `KAFKA_BRIDGE_URL`, bridge REST | catalog-svc `configmap` |
| **Order events (NEW infra to wire)** | a Kafka `orders` topic | `after_plan` → platform (sub-component + link) |

> This feature adds the FIRST money-path domain. Payment is **SIMULATED** in this
> increment (a fake `paid` transition, no external processor) — real Stripe/Adyen is a
> later increment behind the same `Order.status` state machine. Auction settlement
> (winning bid → order) is a follow-up; this increment ships **buy-now checkout**.

## Current Implementation State (grounded)

- Domain today = `Category`, `Listing`, `Bid` only. **No `Order`, no `sell_type`, no checkout.**
- `Listing` has `price`, `seller` (token-set), `highest_bid` (derived from bids).
- Auth enforced: browse public, writes require a valid token; identity from claims.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Buy-now checkout (Priority: P1)
A signed-in buyer purchases a `buy_now` listing; an `Order` is created, marked `paid`
(simulated), and the listing is marked sold so no one else can buy it.

**Why this priority**: This is the MVP of the money path — without it a "store" sells nothing. Independently shippable and demoable.

**Independent Test**: `POST /api/catalog/listings/{id}/checkout/` on a `buy_now` listing → 201 Order in `paid`; a second checkout → 409 (already sold).

**Acceptance Scenarios**:
1. **Given** a `buy_now` listing that is `active`, **When** an authenticated buyer `POST /listings/{id}/checkout/`, **Then** 201 with an `Order` (status `paid`), `OrderItem` snapshotting title+price, buyer = token subject, seller = listing seller.
2. **Given** the same listing already sold, **When** a second buyer checks out, **Then** 409 `already sold` and no second order — enforced under a **row lock** on the listing (same discipline as the bid race).
3. **Given** an anonymous request, **Then** 401/403 (writes require a token).
4. **Given** an `auction` listing (not `buy_now`), **When** checkout, **Then** 409 `not available for direct purchase`.
5. **Given** a buyer trying to buy **their own** listing, **Then** 409 `cannot buy your own listing`.

### User Story 2 — My orders (buyer) & my sales (seller) (Priority: P2)
**Independent Test**: after a checkout, `GET /orders/` as buyer lists it; as the seller, `GET /sales/` lists it.

**Acceptance Scenarios**:
1. **Given** paid orders, **When** buyer `GET /api/catalog/orders/`, **Then** only that buyer's orders return, newest first.
2. **Given** paid orders, **When** seller `GET /api/catalog/sales/`, **Then** only orders for that seller's listings return.
3. **Given** `GET /orders/{id}/` for an order the caller neither bought nor sold, **Then** 404 (no cross-tenant leakage).

### User Story 3 — Order lifecycle transitions (Priority: P3)
Seller marks an order `shipped`; buyer confirms `completed`.

**Acceptance Scenarios**:
1. **Given** a `paid` order, **When** the seller `POST /orders/{id}/ship/`, **Then** status → `shipped` (only the seller may).
2. **Given** a `shipped` order, **When** the buyer `POST /orders/{id}/complete/`, **Then** status → `completed` (only the buyer may).
3. **Given** an illegal transition (e.g. complete before ship), **Then** 409 with the current state.

### Edge Cases
- Concurrent checkout of the same listing → listing row lock ensures exactly one order; the loser gets 409.
- Order-event publish to Kafka fails → the order still persists (201); publish is best-effort with a `published` flag (mirror the bid pattern).
- `sell_type` absent on legacy listings → default `auction` (backward-compatible; existing listings unaffected).

## Functional Requirements
- **FR-1**: `Listing` gains `sell_type` (`auction` | `buy_now`, default `auction`) and `status` (`active` | `sold` | `ended`).
- **FR-2**: `Order` = buyer (token sub), seller, total, status (`pending`→`paid`→`shipped`→`completed`, or `cancelled`), created_at, payment_ref (simulated). `OrderItem` snapshots listing id/title/price at purchase time (price immutability).
- **FR-3**: Checkout MUST take a **row lock** on the listing, verify `sell_type==buy_now` and `status==active`, create the Order + mark listing `sold` **atomically**.
- **FR-4**: Buyer & seller identity MUST come from the verified token, never the request body.
- **FR-5**: Payment is simulated: the `paid` transition sets `payment_ref = "SIMULATED-<uuid>"`. No card data touches the app (keeps this out of PCI scope until a real PSP is a separate service).
- **FR-6**: On `paid`, publish an `order.paid` event to the `orders` Kafka topic (best-effort; order is source of truth).
- **FR-7**: Listing queries MUST expose `sell_type` and `status` so the UI can show Buy-now vs Auction and hide sold items.

## Success Criteria (verify against the live stack)
- **SC-1**: Buy-now on an active listing returns 201 + `paid` Order; the listing then reads `status:sold`.
- **SC-2**: Two concurrent checkouts of one listing → exactly one Order in the DB, the other 409 (verified with parallel requests, like the bid-race test).
- **SC-3**: Buyer sees the order in `/orders/`; seller sees it in `/sales/`; a third party gets 404 on `/orders/{id}/`.
- **SC-4**: An `order.paid` message lands on the `orders` topic (bridge consumer sees it).
- **SC-5**: Existing auctions/bids are unaffected (regression: `/listings/`, `/bid/`, `/bids/stream/` still pass).

## Infrastructure this feature needs (→ `after_plan` routes to the platform)
- **Kafka `orders` topic** (sub-component of `kafka`) + `catalog-svc → orders` link. Everything else reuses existing wiring (catalog-db, valkey, auth). **No new `external_source_code`** — orders is a domain module inside `catalog-svc`.

## Out of scope (explicit — later increments)
Real payment processor (Stripe/Adyen + PCI-isolated payment service) · auction settlement (winning bid → order at auction end) · shipping/tax/addresses · refunds/disputes · seller payouts. Each hangs off this `Order` + its status machine.
