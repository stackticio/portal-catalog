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
import ssl
import threading
import time
import uuid
from contextlib import contextmanager


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


_PRODUCER = None
_PRODUCER_LOCK = threading.Lock()


def _producer():
    """One producer for the whole process. librdkafka keeps the broker connections and
    metadata warm, so a bid pays a network round trip, not a TLS handshake. A producer
    per request did the handshake every time and was the slow path to avoid."""
    global _PRODUCER
    from confluent_kafka import Producer  # imported lazily so the app boots without the wheel

    with _PRODUCER_LOCK:
        if _PRODUCER is None:
            _PRODUCER = Producer(_conf())
        return _PRODUCER


def produce(key, value, topic_name=None, timeout=5):
    """Publish one record (JSON value) over native mTLS and wait for the broker's ack.
    Raises on failure; the caller decides whether that is fatal (bids: no, the bid is
    already saved)."""
    p = _producer()
    result = {}

    def _cb(err, msg):
        result["err"] = err

    p.produce(topic_name or topic(), key=str(key), value=json.dumps(value), callback=_cb)
    remaining = p.flush(timeout)
    if remaining:
        raise RuntimeError(f"kafka delivery timed out after {timeout}s ({remaining} record(s) in flight)")
    if result.get("err") is not None:
        raise RuntimeError(f"kafka delivery failed: {result['err']}")


def client_cn():
    """CN of this app's client certificate = its Kafka principal. The group ACL the stack
    grants is a PREFIX on that CN (kafka topic-users.yaml), so every consumer group this
    app creates must start with it. Read from the mounted cert; env override for tests."""
    override = os.environ.get("KAFKA_CLIENT_CN")
    if override:
        return override
    cert = os.environ.get("KAFKA_SSL_CERTIFICATE_LOCATION")
    if cert:
        try:
            decoded = ssl._ssl._test_decode_cert(cert)  # noqa: SLF001 - stdlib, no extra wheel
            for rdn in decoded.get("subject", ()):
                for key, val in rdn:
                    if key == "commonName":
                        return val
        except Exception:  # noqa: BLE001 - fall through to the stack's default principal
            pass
    return "stacktic-default-client"


@contextmanager
def consumer(group_prefix="sse", poll_timeout_ms=1000, topic_name=None):
    """A short-lived consumer with its OWN group (fan-out: every caller sees every record),
    the native counterpart of the bridge consumer. Yields a poll() returning a list of
    {topic, key, value (parsed JSON), partition, offset}; closes the consumer on exit."""
    from confluent_kafka import Consumer, KafkaError, KafkaException

    conf = _conf()
    conf.update(
        {
            "group.id": f"{client_cn()}-{group_prefix}-{uuid.uuid4().hex[:12]}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "session.timeout.ms": 10000,
        }
    )
    c = Consumer(conf)
    try:
        c.subscribe([topic_name or topic()])

        def poll():
            out = []
            for msg in c.consume(num_messages=100, timeout=poll_timeout_ms / 1000):
                err = msg.error()
                if err is not None:
                    if err.code() == KafkaError._PARTITION_EOF:  # noqa: SLF001 - librdkafka constant
                        continue
                    raise KafkaException(err)
                raw = msg.value()
                try:
                    value = json.loads(raw) if raw is not None else None
                except ValueError:
                    value = {"raw": raw.decode("utf-8", "replace")}
                key = msg.key()
                out.append(
                    {
                        "topic": msg.topic(),
                        "key": key.decode("utf-8", "replace") if key is not None else None,
                        "value": value,
                        "partition": msg.partition(),
                        "offset": msg.offset(),
                    }
                )
            return out

        yield poll
    finally:
        try:
            c.close()
        except Exception:  # noqa: BLE001 - the connection is gone either way
            pass


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
