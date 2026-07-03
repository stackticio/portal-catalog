"""Thin client for the Strimzi Kafka HTTP Bridge (real-time bidding transport).

Django never speaks the native Kafka protocol here — the `external_source_code`
component has no Kafka topic link, so we reach Kafka over the bridge's REST API on
port 8080. The URL comes from the `KAFKA_BRIDGE_URL` env var (set in the catalog-svc
configmap), pointing at the in-cluster bridge Service:

    http://kafka-bridge-bridge-service.kafka.svc.cluster.local:8080

Two roles:
  * produce()          -- a placed bid is published to the `bids` topic.
  * consumer context   -- an SSE connection creates its own consumer group so every
                          browser tab receives every bid (fan-out), then polls records.
"""
import json
import uuid
from contextlib import contextmanager

import requests
from django.conf import settings

# Strimzi bridge media types.
_JSON = "application/vnd.kafka.json.v2+json"
_V2 = "application/vnd.kafka.v2+json"

BRIDGE_URL = (settings.KAFKA_BRIDGE_URL or "").rstrip("/")
TOPIC = settings.KAFKA_BIDS_TOPIC


def is_enabled():
    """Bidding transport is available only when the bridge URL is configured."""
    return bool(BRIDGE_URL)


def produce(key, value, timeout=5):
    """Publish one record to the bids topic. Raises on transport/bridge error."""
    if not BRIDGE_URL:
        raise RuntimeError("KAFKA_BRIDGE_URL is not configured")
    resp = requests.post(
        f"{BRIDGE_URL}/topics/{TOPIC}",
        headers={"Content-Type": _JSON},
        data=json.dumps({"records": [{"key": str(key), "value": value}]}),
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


@contextmanager
def consumer(group_prefix="bids-sse", poll_timeout_ms=1000):
    """Create a short-lived, uniquely-grouped bridge consumer subscribed to bids.

    Each caller gets its OWN consumer group, so all callers see all records
    (fan-out) rather than competing for partitions. The consumer is always
    deleted on exit, even if the caller's generator is closed on disconnect.
    """
    group = f"{group_prefix}-{uuid.uuid4().hex[:12]}"
    name = "django"
    base = f"{BRIDGE_URL}/consumers/{group}"
    instance = f"{base}/instances/{name}"

    create = requests.post(
        base,
        headers={"Content-Type": _V2},
        data=json.dumps(
            {
                "name": name,
                "format": "json",
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
                "fetch.min.bytes": 1,
            }
        ),
        timeout=10,
    )
    create.raise_for_status()
    # Reconstruct the instance URL from our own base (do NOT trust base_uri from the
    # response — it can carry a pod-internal host that isn't routable from here).
    try:
        requests.post(
            f"{instance}/subscription",
            headers={"Content-Type": _V2},
            data=json.dumps({"topics": [TOPIC]}),
            timeout=10,
        ).raise_for_status()

        def poll():
            r = requests.get(
                f"{instance}/records?timeout={poll_timeout_ms}",
                headers={"Accept": _JSON},
                timeout=(poll_timeout_ms / 1000) + 10,
            )
            r.raise_for_status()
            return r.json()  # list of {topic,key,value,partition,offset}

        yield poll
    finally:
        try:
            requests.delete(instance, headers={"Content-Type": _V2}, timeout=5)
        except requests.RequestException:
            pass  # best-effort cleanup; the bridge expires idle consumers anyway
