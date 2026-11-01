"""Shopping assistant and customer support, answered by the stack's private model.

The model is never called directly. The `external_source_code-ai_gateway` link puts four
variables in this service's environment (AI_GATEWAY_URL, AI_GATEWAY_MODEL, AI_GATEWAY_API_KEY,
AI_GATEWAY_API_KEY_HEADER) and every call goes through that APISIX AI gateway, which owns key
auth, the prompt guard, the system prompt it prepends and the token budget. This module does
only what the gateway cannot know: it grounds each answer in THIS marketplace's data.

Two modes:
  shop     the shopper's message is searched in the catalog and the matching ACTIVE listings
           are handed to the model, so it recommends only things that exist, at their real
           price. The same listings go back to the browser as cards.
  support  the model gets the marketplace rules as the code enforces them (bidding, buy-now
           checkout, order states, selling) and, for a signed-in user, their own recent
           orders, sales and bids. Anonymous users get the rules only.

Budget: a small self-hosted model has a short context window (2048 tokens in the reference
stack), shared by the gateway's system prompt, ours, the context, the history and the answer.
Context and history are cut to fixed character budgets before the call (about 4 characters per
token for English text).
"""

import json
import logging
import re

import requests
from django.conf import settings
from django.db.models import F, Max, Q
from django.db.models.functions import Coalesce

from catalog import opensearch_client
from catalog.models import Bid, Listing, Order

log = logging.getLogger(__name__)

MODES = ("shop", "support")

MAX_HISTORY_MESSAGES = 6      # last turns kept (user + assistant)
MAX_MESSAGE_CHARS = 800       # one message, cut beyond this
HISTORY_CHARS = 2400          # all kept turns together
CONTEXT_LISTINGS = 5          # listings handed to the model in shop mode
DESCRIPTION_CHARS = 140       # per listing
SUPPORT_ROWS = 5              # orders / sales / bids each, in support mode

_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "are", "can", "any", "have", "has", "need",
    "want", "looking", "look", "find", "show", "something", "some", "good", "best", "cheap",
    "nice", "please", "help", "buy", "get", "what", "which", "who", "how", "there", "that",
    "this", "these", "those", "from", "under", "below", "less", "than", "max", "maximum",
    "about", "around", "like", "would", "could", "should", "recommend", "suggest", "gift",
    "price", "dollars", "usd", "budget", "item", "items", "listing", "listings", "thing",
}
_PRICE_CAP = re.compile(
    r"(?:under|below|less than|cheaper than|at most|max(?:imum)?|up to|budget(?: of)?|<)\s*\$?\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")


class AssistantError(Exception):
    """A failure the browser is told about in plain words. `code` drives the UI."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def is_enabled():
    return bool(settings.AI_GATEWAY_URL and settings.AI_GATEWAY_MODEL)


# --------------------------------------------------------------------------- grounding: shop


def _keywords(text):
    words = [w.strip("'-") for w in _WORD.findall(text.lower())]
    seen, out = set(), []
    for w in words:
        if w in _STOPWORDS or w.isdigit() or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:8]


def _max_price(text):
    m = _PRICE_CAP.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _active_listings():
    return (
        Listing.objects.filter(status="active")
        .select_related("category")
        .annotate(current_high=Coalesce(Max("bids__amount"), F("price")))
    )


def find_listings(text, limit=CONTEXT_LISTINGS):
    """Active listings that match the shopper's message, best first.

    OpenSearch ranks when it is linked (typo tolerant, same index as /search/); otherwise, or
    when it fails, the database is searched word by word and ranked by how many words match.
    A price cap in the message ("under $200") filters both paths.
    """
    words = _keywords(text)
    cap = _max_price(text)

    if words and opensearch_client.is_enabled():
        try:
            hits = opensearch_client.search(q=" ".join(words), status="active", max_price=cap,
                                            page=1, size=limit)
            by_id = {lst.id: lst for lst in _active_listings().filter(id__in=hits["ids"])}
            ranked = [by_id[i] for i in hits["ids"] if i in by_id]
            if ranked:
                return ranked
        except Exception as exc:  # noqa: BLE001 - degrade to the database, never fail the chat
            log.warning("assistant: opensearch search failed, using the database: %s", exc)

    qs = _active_listings()
    if cap is not None:
        qs = qs.filter(price__lte=cap)
    if not words:
        return list(qs.order_by("-created_at")[:limit])
    cond = Q()
    for w in words:
        cond |= Q(title__icontains=w) | Q(description__icontains=w) | Q(category__name__icontains=w)
    candidates = list(qs.filter(cond).order_by("-created_at")[:50])

    def score(lst):
        hay = f"{lst.title} {lst.description} {lst.category.name if lst.category else ''}".lower()
        return sum(w in hay for w in words)

    candidates.sort(key=score, reverse=True)  # stable: newest first among equal scores
    return candidates[:limit]


def _price_phrase(lst):
    if lst.sell_type == "buy_now":
        return f"buy now ${lst.price:.2f}"
    return f"auction, current high ${lst.current_high:.2f}"


def listing_card(lst):
    """What the browser shows under the answer."""
    return {
        "id": lst.id,
        "title": lst.title,
        "price": f"{lst.current_high:.2f}" if lst.sell_type == "auction" else f"{lst.price:.2f}",
        "sell_type": lst.sell_type,
        "condition": lst.condition,
        "category": lst.category.name if lst.category else "",
    }


def _listing_line(lst):
    desc = " ".join((lst.description or "").split())[:DESCRIPTION_CHARS]
    parts = [f"[#{lst.id}] {lst.title}", lst.category.name if lst.category else "",
             _price_phrase(lst), lst.get_condition_display().lower(), lst.location, desc]
    return " | ".join(p for p in parts if p)


SHOP_PROMPT = (
    "Mode: shopping assistant. Recommend ONLY listings from the CATALOG below and refer to each "
    "one as [#id] with its title and price exactly as given. Never invent listings, prices, "
    "stock, shipping or seller details. If nothing fits, say so in one sentence and suggest a "
    "better search phrase. Keep answers under 120 words; use a short list for several items."
)


def shop_context(question):
    listings = find_listings(question)
    if listings:
        body = "\n".join(_listing_line(lst) for lst in listings)
    else:
        body = "(no active listing matches this request)"
    return f"{SHOP_PROMPT}\n\nCATALOG (active listings matching the request):\n{body}", listings


# --------------------------------------------------------------------------- grounding: support

# Written from the code that enforces each rule (catalog/views.py: ListingViewSet.bid / checkout
# / end / watch, OrderViewSet.ship / complete). Change a rule there, change it here.
MARKETPLACE_RULES = """\
- Bidding: sign in, open an auction and enter an amount. A bid must be higher than the current \
high; with no bids yet the starting price is the current high. Every viewer sees new bids live.
- Buy now: listings marked "Buy now" are bought instantly when signed in. The listing must still \
be active, and nobody can buy their own listing. Payment is simulated in this marketplace: no \
card details are ever asked for, and the order is created as paid.
- Order states: paid -> shipped (the seller marks it shipped) -> completed (the buyer confirms it \
arrived). Orders can also be pending or cancelled.
- Orders page: "Orders" in the top bar lists what you bought and what you sold; sellers press Ship, \
buyers press Complete.
- Watchlist: press Watch on any listing to follow it; "Account" shows your watchlist, listings and bids.
- Selling: signed-in users create listings. Only the seller can edit, delete or end a listing; \
ending stops an active listing.
- Not available in this marketplace: refunds, disputes, returns, messaging between users and \
cancelling a paid order. Say so plainly and never promise them.
- The assistant cannot bid, buy, ship or change anything; it explains how to do it."""

SUPPORT_PROMPT = (
    "Mode: customer support. Answer ONLY from the RULES and, when present, the USER DATA below. "
    "Use order numbers and amounts exactly as given. If the answer is not there, say you do not "
    "know and point to the page that shows it. Keep answers under 120 words."
)


def _order_line(order, role):
    titles = ", ".join(i.title for i in order.items.all()) or "no items"
    who = f"seller {order.seller}" if role == "bought" else f"buyer {order.buyer}"
    return (f"Order #{order.id} ({role}) | {order.get_status_display().lower()} | "
            f"${order.total:.2f} | {order.created_at:%Y-%m-%d} | {titles} | {who}")


def _bid_lines(username):
    bids = (Bid.objects.filter(bidder=username).select_related("listing")
            .order_by("-created_at")[:SUPPORT_ROWS])
    lines = []
    for b in bids:
        high = b.listing.highest_bid
        standing = "leading" if b.amount >= high else f"outbid, current high ${high:.2f}"
        lines.append(f"Bid ${b.amount:.2f} on [#{b.listing_id}] {b.listing.title} | "
                     f"{b.created_at:%Y-%m-%d} | listing {b.listing.status} | {standing}")
    return lines


def support_context(user):
    parts = [SUPPORT_PROMPT, "", "RULES:", MARKETPLACE_RULES]
    if getattr(user, "is_authenticated", False):
        me = user.username
        bought = Order.objects.filter(buyer=me).prefetch_related("items").order_by("-created_at")[:SUPPORT_ROWS]
        sold = Order.objects.filter(seller=me).prefetch_related("items").order_by("-created_at")[:SUPPORT_ROWS]
        rows = [_order_line(o, "bought") for o in bought] + [_order_line(o, "sold") for o in sold]
        rows += _bid_lines(me)
        parts += ["", f"USER DATA for {me} (most recent first):", "\n".join(rows) or "(no orders, sales or bids yet)"]
    else:
        parts += ["", "USER DATA: the user is not signed in. For questions about their own orders "
                      "or bids, ask them to sign in first."]
    return "\n".join(parts)


# --------------------------------------------------------------------------- conversation


def clean_history(raw):
    """Only user/assistant turns from the browser, newest last, within the budgets.

    A browser-sent `system` turn is dropped: the system prompts belong to the gateway and to this
    module, never to the client. The kept history must end with the user's question."""
    if not isinstance(raw, list):
        raise AssistantError("bad_request", "messages must be a list")
    turns = []
    for m in raw[-(MAX_HISTORY_MESSAGES * 2):]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        turns.append({"role": m["role"], "content": content.strip()[:MAX_MESSAGE_CHARS]})
    if not turns or turns[-1]["role"] != "user":
        raise AssistantError("bad_request", "the last message must be the user's question")

    kept, used = [], 0
    for t in reversed(turns[-MAX_HISTORY_MESSAGES:]):
        if kept and used + len(t["content"]) > HISTORY_CHARS:
            break
        kept.append(t)
        used += len(t["content"])
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


def build_messages(mode, history, user):
    """(messages for the model, listings used). The gateway prepends its own system prompt."""
    question = history[-1]["content"]
    if mode == "shop":
        system, listings = shop_context(question)
    else:
        system, listings = support_context(user), []
    return [{"role": "system", "content": system}, *history], listings


# --------------------------------------------------------------------------- gateway call


def _error_for(status, body):
    text = (body or "")[:300].lower()
    if status in (401, 403):
        return AssistantError("not_authorised", "The assistant is not authorised at the AI gateway.")
    if status == 429:
        return AssistantError("busy", "The assistant is at its limit right now. Please try again in a few minutes.")
    if status == 400 and ("context length" in text or ("maximum" in text and "token" in text)):
        return AssistantError("too_long", "This conversation is too long for the assistant. Start a new chat.")
    if status == 400:
        return AssistantError("blocked", "The assistant can't help with that request.")
    return AssistantError("unavailable", "The assistant is unavailable right now.")


def stream_reply(messages):
    """Yield ("delta", text) while the model writes, then ("usage", {...}) when it reports usage.

    OpenAI-compatible streaming through the gateway. `stream_options.include_usage` asks the
    model server for a final usage chunk, which a token-based gateway limit needs to count a
    streamed answer."""
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if settings.AI_GATEWAY_API_KEY:
        headers[settings.AI_GATEWAY_API_KEY_HEADER] = settings.AI_GATEWAY_API_KEY
    body = {
        "model": settings.AI_GATEWAY_MODEL,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": settings.AI_ASSISTANT_MAX_TOKENS,
        "temperature": 0.3,
    }
    try:
        resp = requests.post(f"{settings.AI_GATEWAY_URL}/chat/completions", json=body, headers=headers,
                             stream=True, timeout=(5, settings.AI_ASSISTANT_TIMEOUT))
    except requests.RequestException as exc:
        log.warning("assistant: gateway unreachable: %s", exc)
        raise AssistantError("unavailable", "The assistant is unavailable right now.") from exc

    with resp:
        if resp.status_code != 200:
            err = _error_for(resp.status_code, resp.text)
            log.warning("assistant: gateway answered %s (%s): %s", resp.status_code, err.code, resp.text[:200])
            raise err
        resp.encoding = "utf-8"
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    yield ("usage", chunk["usage"])
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield ("delta", piece)
        except requests.RequestException as exc:
            log.warning("assistant: stream interrupted: %s", exc)
            raise AssistantError("interrupted", "The answer was interrupted. Please ask again.") from exc
