"""Thin client for OpenSearch — the catalog's full-text SEARCH backend.

The `external_source_code-opensearch` link scopes this app to the `listings*` index
(user `osuser`, read+write) and injects the connection into the catalog-svc secret:

    OPENSEARCH_URL, OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD,
    OPENSEARCH_INDICES (permission scope), OPENSEARCH_VERIFY_CERTS

Two roles:
  * index_listing() / delete_listing() -- keep the `listings` index in sync with the
    DB (called from post_save/post_delete signals; best-effort, never blocks a write).
  * search() -- rank + filter listing ids for the /search endpoint; the view hydrates
    those ids from the DB so results match the rest of the API exactly.

OpenSearch is OPTIONAL: with no URL configured (local dev, or the cluster down) the
client reports is_enabled()==False and the search view falls back to a DB query — the
same infra-optional pattern the app uses for Postgres/Redis/Kafka.
"""
import logging

from django.conf import settings

try:
    from opensearchpy import OpenSearch, helpers
    from opensearchpy.exceptions import NotFoundError, OpenSearchException
except Exception:  # opensearch-py absent in a minimal local env
    OpenSearch = None
    helpers = None
    NotFoundError = OpenSearchException = Exception

log = logging.getLogger(__name__)

INDEX = settings.OPENSEARCH_LISTINGS_INDEX

# text = what a user types into the search box; keyword = exact filters / sorting.
_MAPPING = {
    "mappings": {
        "properties": {
            "title": {"type": "text"},
            "description": {"type": "text"},
            "category_name": {"type": "text"},
            "location": {"type": "text"},
            "seller": {"type": "keyword"},
            "category": {"type": "keyword"},
            "condition": {"type": "keyword"},
            "sell_type": {"type": "keyword"},
            "status": {"type": "keyword"},
            "image_key": {"type": "keyword", "index": False},
            "price": {"type": "double"},
            "created_at": {"type": "date"},
        }
    }
}

_client = None
_client_built = False
_index_ready = False


def _get_client():
    """Lazily build (and cache) the client. Returns None when unconfigured so callers
    can degrade gracefully instead of crashing."""
    global _client, _client_built
    if _client_built:
        return _client
    _client_built = True
    if not (OpenSearch and settings.OPENSEARCH_URL and settings.OPENSEARCH_USERNAME):
        _client = None
        return None
    _client = OpenSearch(
        hosts=[settings.OPENSEARCH_URL],
        http_auth=(settings.OPENSEARCH_USERNAME, settings.OPENSEARCH_PASSWORD),
        verify_certs=settings.OPENSEARCH_VERIFY_CERTS,
        ssl_show_warn=False,
        timeout=5,
        max_retries=1,
        retry_on_timeout=True,
    )
    return _client


def is_enabled():
    return _get_client() is not None


def _document(listing):
    """The searchable projection of a Listing (catalog fields only — live bid state
    stays in the DB and is added back when a result is hydrated)."""
    return {
        "title": listing.title,
        "description": listing.description,
        "seller": listing.seller,
        "category": listing.category.slug if listing.category_id else "",
        "category_name": listing.category.name if listing.category_id else "",
        "condition": listing.condition,
        "location": listing.location,
        "sell_type": listing.sell_type,
        "status": listing.status,
        "image_key": listing.image_key,
        "price": float(listing.price),
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
    }


def ensure_index():
    """Create the listings index with its mapping if it doesn't exist (idempotent)."""
    client = _get_client()
    if client is None:
        return False
    if not client.indices.exists(index=INDEX):
        client.indices.create(index=INDEX, body=_MAPPING)
    return True


def _ensure_ready():
    """Create the index with our explicit mapping once per process, BEFORE the first
    write. Without this a signal-driven write could auto-create the index with a dynamic
    mapping (keyword filters become text, price becomes long), silently breaking search."""
    global _index_ready
    if _index_ready:
        return
    if ensure_index():
        _index_ready = True


def index_listing(listing):
    """Upsert one listing document. Best-effort: a failed index is logged and swallowed
    so it never breaks the DB write that triggered it."""
    client = _get_client()
    if client is None:
        return
    try:
        _ensure_ready()
        client.index(index=INDEX, id=listing.id, body=_document(listing))
    except OpenSearchException as exc:  # noqa: BLE001 - indexing is best-effort
        log.warning("opensearch index failed for listing %s: %s", listing.id, exc)


def delete_listing(listing_id):
    """Remove a listing document (best-effort; a missing doc is fine)."""
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(index=INDEX, id=listing_id)
    except NotFoundError:
        pass
    except OpenSearchException as exc:  # noqa: BLE001
        log.warning("opensearch delete failed for listing %s: %s", listing_id, exc)


def search(q="", category="", condition="", sell_type="", status="active",
           min_price=None, max_price=None, sort="relevance", page=1, size=24):
    """Rank + filter listings, returning ``{"ids": [...ranked...], "total": N}``.

    Full-text over title/category/description/location with typo tolerance, plus keyword
    filters and a price range; sortable by relevance/newest/price. Raises on transport
    error so the view can fall back to a DB search.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("OpenSearch is not configured")

    filters = []
    if category:
        filters.append({"term": {"category": category}})
    if condition:
        filters.append({"term": {"condition": condition}})
    if sell_type:
        filters.append({"term": {"sell_type": sell_type}})
    if status and status != "all":
        filters.append({"term": {"status": status}})
    price_range = {}
    if min_price is not None:
        price_range["gte"] = min_price
    if max_price is not None:
        price_range["lte"] = max_price
    if price_range:
        filters.append({"range": {"price": price_range}})

    if q:
        must = {
            "multi_match": {
                "query": q,
                "fields": ["title^3", "category_name^2", "description", "location"],
                "fuzziness": "AUTO",  # typo tolerance ("vintge camra" -> "vintage camera")
            }
        }
    else:
        must = {"match_all": {}}

    sort_map = {
        "newest": [{"created_at": "desc"}],
        "price_asc": [{"price": "asc"}],
        "price_desc": [{"price": "desc"}],
    }
    # Relevance (_score) only means something with a query; an empty query sorts newest.
    sort_clause = sort_map.get(sort) or ([] if q else [{"created_at": "desc"}])

    body = {
        "query": {"bool": {"must": must, "filter": filters}},
        "_source": False,          # only ids are needed; the view hydrates from the DB
        "from": max(0, (page - 1) * size),
        "size": size,
    }
    if sort_clause:
        body["sort"] = sort_clause

    resp = client.search(index=INDEX, body=body)
    hits = resp.get("hits", {})
    total = hits.get("total", 0)
    total = total.get("value", 0) if isinstance(total, dict) else total
    ids = [int(h["_id"]) for h in hits.get("hits", [])]
    return {"ids": ids, "total": total}


def reindex_all(listings):
    """Bulk (re)index a queryset of listings; returns the count indexed."""
    client = _get_client()
    if client is None:
        raise RuntimeError("OpenSearch is not configured")
    ensure_index()
    actions = ({"_index": INDEX, "_id": lst.id, "_source": _document(lst)} for lst in listings)
    if helpers is not None:
        ok, _ = helpers.bulk(client, actions, stats_only=True)
        return ok
    count = 0
    for lst in listings:
        client.index(index=INDEX, id=lst.id, body=_document(lst))
        count += 1
    return count
