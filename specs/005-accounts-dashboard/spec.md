# Feature Specification: Accounts & Dashboard (My eBay)

**Feature Branch**: `005-accounts-dashboard`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "A real bidding portal where users have an account with their listed items, pending bids, settings — like eBay." → the per-user self-service home behind the existing Keycloak login.

## Stack Context (grounded — from the constitution + live topology, NOT assumed)

Per Constitution Principle V, every platform fact below is read from the stack, not guessed.

| Fact | Value | Source |
|------|-------|--------|
| Framework / port | `django`, container `:8000` | constitution §III |
| Datastore | `DATABASE_URL` → `catalog-db` (cnpg) | link `external_source_code-db` |
| Identity | Keycloak OIDC already wired & working: `catalog/auth.py::TokenUser` (`sub`, `preferred_username`, roles); writes require a valid bearer token, browse is public (`IsAuthenticatedOrReadOnly`) | live app + `auth_config/callback/register` |
| Cache | `REDIS_URL` → valkey (optional, for per-user aggregates) | link `external_source_code-valkey` (live topology) |
| Exposure | `apisix-external_source_code` route (`api.vibe.source-lab.io`) | inbound apisix link |

> **No new infrastructure and no new component.** This is a domain module inside the
> existing `catalog-svc` (+ its UI in `portal-frontend`), exactly like `004-orders`.
> Identity already exists — nothing to wire. Per Constitution Principle I, if a plan step
> here proposes a manifest/link, that is a violation: everything in this spec is code.

## Current State (grounded — read from the repo, this is what already ships)

- ✅ Real login/register via Keycloak; identity from the verified token.
- ✅ Browse/search catalog, place live bids (SSE over Kafka `bids`), Buy-now checkout.
- ✅ **My Orders** area: `GET /orders/` (bought) + `GET /orders/sales/` (sold) with ship/complete — so a per-user account surface already exists.
- ❌ No **My Listings** — `ListingViewSet` returns all listings (`?category=` only); no seller-scoped view.
- ❌ No **My Bids** — `Bid` has `bidder`, but nothing aggregates "auctions I'm in" with winning/outbid.
- ❌ No **Profile/Settings** — no `Profile` model, no `/me/`.
- ❌ No **Watchlist** — no model, no endpoints.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - My Listings (seller dashboard) (Priority: P1)

A signed-in seller opens their account and sees every item **they** listed, with each item's status (active / sold / ended), current high bid, and number of bids — and can end an active listing.

**Why this priority**: "Their listed items" is the first thing the user named; without a seller view of one's own inventory there is no "account," only an anonymous catalog.

**Independent Test**: as user U, `GET /me/listings/` returns only listings whose seller is U's token identity, each carrying status + highest bid + bid count; a second user never sees U's items.

**Acceptance Scenarios**:

1. **Given** listings created by me and by others, **When** I request my listings, **Then** only mine return (matched on the verified token identity, never a query param), newest first.
2. **Given** my listings span active/sold/ended, **When** I filter by status, **Then** only that status returns, each with current high bid and bid count.
3. **Given** an anonymous request, **When** my-listings is requested, **Then** it is rejected (401/403).
4. **Given** one of my active listings, **When** I end it, **Then** its status becomes `ended`; a non-owner attempting the same gets a not-found (no cross-tenant leakage).

---

### User Story 2 - My Bids (winning vs outbid) (Priority: P1)

A signed-in buyer sees every auction they have bid on, each clearly labelled **winning** or **outbid**, showing their own highest bid and the auction's current high.

**Why this priority**: "Pending bids" is the second thing the user named and the core buyer-side value; it is the reason to come back to the site.

**Independent Test**: after U bids on listings A and B, `GET /me/bids/` returns A and B once each, with `my_max`, `current_high`, and a state of `winning` or `outbid`.

**Acceptance Scenarios**:

1. **Given** I placed bids on several auctions, **When** I request my bids, **Then** each distinct auction I bid on appears once, with my highest bid and the auction's current high.
2. **Given** my bid is the current high, **Then** the auction shows `winning`; **Given** someone outbid me, **Then** it shows `outbid`.
3. **Given** an auction I bid on has ended, **Then** it shows `won` (I was high) or `lost`.
4. **Given** I filter to `winning`, **Then** only auctions I'm currently leading return.
5. **Given** an anonymous request, **Then** it is rejected (401/403).

---

### User Story 3 - Profile & Settings (Priority: P2)

A signed-in user views and edits their account settings — display name, contact email, default location, and notification preferences (notify when outbid / when an item sells). Identity fields (username/subject) come from Keycloak and are read-only.

**Why this priority**: "Settings" is the third thing the user named; it also anchors notifications and the profile every other feature can reference.

**Independent Test**: `GET /me/` returns my profile (auto-created on first read from token claims); `PATCH /me/` updates the editable fields and they persist across requests.

**Acceptance Scenarios**:

1. **Given** a first-ever signed-in user, **When** they read their profile, **Then** a profile is created once from the token claims and returned.
2. **Given** my profile, **When** I update display name / contact / location / notification prefs, **Then** those persist; identity fields sent in the body are ignored.
3. **Given** any user, **When** they try to read or write another user's profile, **Then** there is no route to do so — a user can only touch their own.
4. **Given** an anonymous request, **Then** it is rejected (401/403).

---

### User Story 4 - Watchlist (Priority: P2)

A signed-in user follows listings they don't own to track them in one place, and unfollows anytime.

**Why this priority**: Rounds out the eBay-style account; lower priority than owning/bidding but expected in a "real" portal.

**Independent Test**: watch a listing, then `GET /me/watchlist/` returns it with live listing fields; unwatch removes it; watching twice does not duplicate or error.

**Acceptance Scenarios**:

1. **Given** a listing, **When** I watch it, **Then** it is added to my watchlist idempotently (a second watch is a no-op).
2. **Given** watched listings, **When** I request my watchlist, **Then** they return newest-watched-first with current high bid, status, and image.
3. **Given** a watched listing, **When** I unwatch it, **Then** it is removed from my watchlist.
4. **Given** an anonymous request to any watch action, **Then** it is rejected (401/403).

### Edge Cases

- Demo-era rows: legacy `Listing.seller` / `Bid.bidder` are free-text from before enforced auth; `/me/*` matches on the token username (what new writes set) — pre-auth rows may map to no real account. Documented, not silently mishandled.
- Concurrent first-time profile reads MUST create at most one profile per subject.
- Watching a listing that is later deleted → the watch entry disappears (no dangling reference), not a 500.
- `/me/bids/` MUST aggregate my max + current high per auction in a bounded number of queries (no per-listing query loop).
- When identity is unavailable (local dev, OIDC off) → `/me/*` returns 401, never a fabricated anonymous account.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST let a signed-in user retrieve only their own listings, each with status, current high bid, and bid count, filterable by status.
- **FR-002**: The system MUST let a listing's owner end their own active listing (status → ended); non-owners MUST NOT be able to, and MUST NOT learn the listing exists via this action.
- **FR-003**: The system MUST let a signed-in user retrieve the auctions they have bid on, one row per auction, each with the user's highest bid, the auction's current high, and a derived state (winning / outbid / won / lost), filterable by state.
- **FR-004**: The system MUST provide a per-user profile with editable display name, contact email, default location, and notification preferences; it MUST be auto-created on first access from verified token claims.
- **FR-005**: Users MUST be able to update only their own profile's editable fields; identity fields MUST be immutable and token-derived, never accepted from the request body.
- **FR-006**: The system MUST let a signed-in user watch and unwatch any listing, idempotently, and retrieve their watchlist with live listing fields.
- **FR-007**: Every account endpoint MUST derive identity from the verified token; identity MUST NOT be spoofable via query or body, and anonymous access MUST be rejected.
- **FR-008**: All new persistence MUST use the platform-generated `DATABASE_URL` (Constitution Principle II); no new endpoint, credential, or connection string in code or manifests.
- **FR-009**: The change MUST be additive — existing catalog, bidding, checkout, and orders behaviour MUST be unaffected (backward compatible schema migration).

### Key Entities *(include if feature involves data)*

- **Profile**: a user's account record, one per Keycloak subject. Holds identity (subject, username — read-only, from token) plus editable display name, contact email, default location, and notification preferences. Auto-created on first access.
- **Watchlist entry**: a user (by subject) following a listing they don't own; unique per (user, listing); ordered by when watched.
- **Reused (already exist)**: `Listing` (owner = `seller`, `status`, `highest_bid`), `Bid` (`bidder`, `amount`), `Order` (buyer/seller) — this feature only reads and aggregates these per-user; it does not change them.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Signed in as U, my-listings shows exactly U's listings and no one else's; verified against the live stack.
- **SC-002**: U bids on an auction, a rival outbids → U's my-bids row flips from `winning` to `outbid` within one refresh.
- **SC-003**: First `/me/` access creates a profile; a settings change persists across sessions.
- **SC-004**: Watch → appears in watchlist; unwatch → gone; double-watch neither errors nor duplicates.
- **SC-005**: Every account endpoint returns 401/403 when anonymous; identity cannot be set via body/query.
- **SC-006**: Regression — catalog browse, bid, live SSE, checkout, orders, and sales all still pass their existing checks (`stack_test`).

## Assumptions

- Keycloak login already works and is the sole identity source (verified in the running app); this feature adds no auth mechanism, only consumes the token.
- "My Listings" scopes on the token username, matching how `perform_create` already stamps `seller`; demo-era free-text rows are out of scope for account mapping.
- Notifications in this increment are stored preferences only; actually delivering emails/push is a later feature (an event/SMTP path), out of scope here.
- Watchlist is per-user follow only; saved-search alerts, seller ratings, and messaging are explicitly out of scope (later increments hanging off `Profile`).
- Sell-a-listing-with-photo (creating the items that populate "My Listings") is covered by the existing specs `002-listing-management` (backend upload) + `002-sell-listing-ui` (frontend); this spec depends on but does not re-specify it.
