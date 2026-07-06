"""Native Kafka client (mTLS), replacing the HTTP-bridge hop.

Connects straight to the Strimzi broker over the native protocol using the
mTLS material Stacktic mounts from the external_source_code-topic link:

    KAFKA_BOOTSTRAP_SERVERS          e.g. kafka-kafka-bootstrap.strimzi.svc:9093
    KAFKA_SECURITY_PROTOCOL=SSL
    KAFKA_SSL_CA_LOCATION            /etc/kafka/certs/ca.crt   (trust the broker)
    KAFKA_SSL_CERTIFICATE_LOCATION   /etc/kafka/certs/user.crt (this app's client cert)
    KAFKA_SSL_KEY_LOCATION           /etc/kafka/certs/user.key
    KAFKA_TOPICS                     e.g. bids

The client cert's CN (stacktic-default-client) is the Kafka principal; the
KafkaUser ACLs generated on the kafka side authorize it to Read/Write the topic.
"""
import json
import os
import time


def bootstrap():
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS")


def topic():
    return (os.environ.get("KAFKA_TOPICS") or os.environ.get("KAFKA_BIDS_TOPIC") or "bids").split(",")[0]


def is_native_enabled():
    """Native transport is available only when the broker + certs are wired."""
    return bool(bootstrap()) and os.environ.get("KAFKA_SECURITY_PROTOCOL") == "SSL"


def _conf():
    conf = {
        "bootstrap.servers": bootstrap(),
        "security.protocol": os.environ.get("KAFKA_SECURITY_PROTOCOL", "SSL"),
    }
    for env_key, rd_key in (
        ("KAFKA_SSL_CA_LOCATION", "ssl.ca.location"),
        ("KAFKA_SSL_CERTIFICATE_LOCATION", "ssl.certificate.location"),
        ("KAFKA_SSL_KEY_LOCATION", "ssl.key.location"),
    ):
        val = os.environ.get(env_key)
        if val:
            conf[rd_key] = val
    return conf


def _producer():
    from confluent_kafka import Producer  # imported lazily so the app boots without the wheel
    return Producer(_conf())


def produce(key, value, timeout=10):
    """Publish one record to the bids topic over native mTLS. Raises on failure."""
    p = _producer()
    result = {}

    def _cb(err, msg):
        result["err"] = err

    p.produce(topic(), key=str(key), value=json.dumps(value), callback=_cb)
    p.flush(timeout)
    if result.get("err") is not None:
        raise RuntimeError(f"kafka delivery failed: {result['err']}")


def health(timeout=10, do_produce=True):
    """End-to-end mTLS check: connect -> fetch topic metadata (Describe ACL) ->
    optionally produce one record (Write ACL). Returns a JSON-able dict."""
    out = {
        "transport": "native-mtls",
        "broker": bootstrap(),
        "topic": topic(),
        "protocol": os.environ.get("KAFKA_SECURITY_PROTOCOL"),
        "ca": os.environ.get("KAFKA_SSL_CA_LOCATION"),
        "cert": os.environ.get("KAFKA_SSL_CERTIFICATE_LOCATION"),
    }
    if not is_native_enabled():
        out.update(ok=False, error="native kafka not configured (need KAFKA_BOOTSTRAP_SERVERS + SSL)")
        return out
    try:
        from confluent_kafka import Producer  # noqa: F401 - surface a clear import error
        p = _producer()
        # 1) metadata fetch proves the mTLS handshake + that the client cert is trusted
        md = p.list_topics(topic=topic(), timeout=timeout)
        tmeta = md.topics.get(topic())
        if tmeta is None or tmeta.error is not None:
            out.update(ok=False, error=f"topic metadata error: {getattr(tmeta, 'error', 'topic not found')}")
            return out
        out["partitions"] = len(tmeta.partitions)
        out["brokers"] = [f"{b.host}:{b.port}" for b in md.brokers.values()]
        # 2) produce proves the Write ACL for principal CN=stacktic-default-client
        if do_produce:
            delivered = {}
            p.produce(
                topic(),
                key="health",
                value=json.dumps({"ts": time.time(), "src": "kafka_native.health"}),
                callback=lambda err, msg: delivered.update(err=err, partition=(msg.partition() if msg else None)),
            )
            p.flush(timeout)
            if delivered.get("err") is not None:
                out.update(ok=False, error=f"produce failed: {delivered['err']}")
                return out
            out["produced_partition"] = delivered.get("partition")
        out.update(ok=True)
        return out
    except Exception as exc:  # noqa: BLE001 - report the exact failure to the caller
        out.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        return out
