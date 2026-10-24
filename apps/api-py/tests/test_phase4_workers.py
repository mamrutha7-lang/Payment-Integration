import math

import pytest

from app.workers.contracts import EventEnvelope
from app.workers.embedding_worker import PermanentEmbeddingError, validate_vector
from app.workers.transport import InMemoryTransport


def test_event_envelope_round_trips_required_identity():
    envelope = EventEnvelope.from_dict(
        {
            "event_id": "evt-1",
            "event_type": "knowledge.embedding.requested",
            "event_version": 1,
            "aggregate_type": "domain_job",
            "aggregate_id": "job-1",
            "actor_id": None,
            "tenant_id": "tenant-1",
            "request_id": "req-1",
            "correlation_id": "corr-1",
            "payload": {"job_id": "job-1"},
        }
    )
    assert envelope.as_dict()["event_id"] == "evt-1"
    assert envelope.payload == {"job_id": "job-1"}


def test_event_envelope_rejects_unsupported_version():
    with pytest.raises(ValueError, match="unsupported event version"):
        EventEnvelope.from_dict(
            {
                "event_id": "evt-1",
                "event_type": "x",
                "event_version": 99,
                "aggregate_type": "x",
                "aggregate_id": "x",
                "payload": {},
            }
        )


def test_vector_validation_requires_exact_dimension_and_finite_numbers():
    assert validate_vector([1, 0.5, -2], 3) == [1.0, 0.5, -2.0]
    with pytest.raises(PermanentEmbeddingError, match="dimension mismatch"):
        validate_vector([1, 2], 3)
    with pytest.raises(PermanentEmbeddingError, match="non-finite"):
        validate_vector([1, math.inf, 2], 3)


def test_in_memory_transport_preserves_at_least_once_boundary():
    transport = InMemoryTransport()
    message_id = transport.publish("queue://default", {"event_id": "evt-1"})
    message = transport.receive("queue://default", max_messages=1, wait_seconds=0)[0]
    assert message["MessageId"] == message_id
    assert transport.deleted == []
    transport.delete("queue://default", message["ReceiptHandle"])
    assert len(transport.deleted) == 1
