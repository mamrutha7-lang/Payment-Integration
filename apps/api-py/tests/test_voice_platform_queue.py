"""The candidate ingest: validation, SQS push/pull routing and the S3-trigger
Lambda — all against fakes, no AWS."""

from __future__ import annotations

import json

import pytest

from app.voice_platform.queue import lambda_handler
from app.voice_platform.queue.sqs import CandidateQueue, QueueNotConfigured
from app.voice_platform.queue.validation import (
    Candidate,
    CandidateValidationError,
    normalize_degree,
    parse_bulk,
    partition,
    queue_message,
    specialization_key,
    validate_candidate,
)


class FakeSQS:
    def __init__(self, *, fail_ids: set[str] | None = None) -> None:
        self.sent: dict[str, list[dict]] = {}
        self.deleted: list[tuple[str, str]] = []
        self.fail_ids = fail_ids or set()
        self._counter = 0

    def send_message_batch(self, *, QueueUrl: str, Entries: list[dict]) -> dict:
        assert len(Entries) <= 10, "SQS batches are capped at ten"
        ok, bad = [], []
        for entry in Entries:
            body = json.loads(entry["MessageBody"])
            if body["candidate"]["external_id"] in self.fail_ids:
                bad.append({"Id": entry["Id"], "Message": "boom", "SenderFault": True, "Code": "Test"})
                continue
            self._counter += 1
            self.sent.setdefault(QueueUrl, []).append(body)
            ok.append({"Id": entry["Id"], "MessageId": f"m{self._counter}"})
        return {"Successful": ok, "Failed": bad}

    def receive_message(self, *, QueueUrl: str, **_: object) -> dict:
        bodies = self.sent.get(QueueUrl, [])
        return {
            "Messages": [
                {"MessageId": f"r{i}", "ReceiptHandle": f"h{i}", "Body": json.dumps(b)}
                for i, b in enumerate(bodies)
            ]
        }

    def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append((QueueUrl, ReceiptHandle))


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.puts: list[tuple[str, str, bytes]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        class _Body:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self) -> bytes:
                return self._data

        return {"Body": _Body(self.objects[Key])}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: object) -> None:
        self.puts.append((Bucket, Key, Body))


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("ug", "UG"), ("Undergraduate", "UG"), ("B.Tech", "UG"), ("PG", "PG"), ("MTech", "PG"), ("post graduate", "PG")],
)
def test_degree_aliases_normalise(raw: str, expected: str) -> None:
    assert normalize_degree(raw) == expected


def test_an_unknown_degree_is_refused_with_the_field_named() -> None:
    with pytest.raises(CandidateValidationError) as exc:
        normalize_degree("diploma")
    assert exc.value.field == "degree_level"


def test_specialization_keys_are_slugs() -> None:
    assert specialization_key("BSc AI") == "bsc-ai"
    assert specialization_key("  MTech  Data Science ") == "mtech-data-science"


def test_a_spreadsheet_row_with_aliased_columns_validates() -> None:
    row = {"USN": "1BG24MBA001", "Full Name": "Asha  Rao", "Degree": "Undergraduate",
           "Track": "BSc AI", "E-mail": "ASHA@bgscet.ac.in", "ignored": "x"}
    c = validate_candidate(row)
    assert c == Candidate("1BG24MBA001", "Asha Rao", "UG", "bsc-ai", "asha@bgscet.ac.in", None)


def test_the_allowed_catalogue_rejects_an_unknown_track() -> None:
    row = {"external_id": "X1", "name": "Some One", "degree_level": "PG", "specialization": "Astrology"}
    with pytest.raises(CandidateValidationError) as exc:
        validate_candidate(row, allowed_specializations={"PG": {"mtech-data-science"}})
    assert exc.value.field == "specialization"
    assert "mtech-data-science" in exc.value.message


@pytest.mark.parametrize(
    "row,field",
    [
        ({"name": "A B", "degree_level": "UG", "specialization": "x"}, "external_id"),
        ({"external_id": "bad id!", "name": "A B", "degree_level": "UG", "specialization": "x"}, "external_id"),
        ({"external_id": "ok", "name": "A", "degree_level": "UG", "specialization": "x"}, "name"),
        ({"external_id": "ok", "name": "A B", "degree_level": "UG"}, "specialization"),
        ({"external_id": "ok", "name": "A B", "degree_level": "UG", "specialization": "x", "email": "nope"}, "email"),
    ],
)
def test_each_required_field_is_reported_by_name(row: dict, field: str) -> None:
    with pytest.raises(CandidateValidationError) as exc:
        validate_candidate(row)
    assert exc.value.field == field


def test_parse_bulk_reads_csv_json_and_jsonl() -> None:
    csv = b"\xef\xbb\xbfusn,name,degree,specialization\n1,Asha Rao,UG,BSc AI\n,,,\n"
    assert parse_bulk(csv, "roster.csv") == [{"usn": "1", "name": "Asha Rao", "degree": "UG", "specialization": "BSc AI"}]
    obj = json.dumps({"candidates": [{"external_id": "2", "name": "B C", "degree_level": "PG", "specialization": "x"}]}).encode()
    assert len(parse_bulk(obj, "roster.json")) == 1
    lines = b'{"external_id":"3"}\n\n{"external_id":"4"}\n'
    assert [r["external_id"] for r in parse_bulk(lines, "roster.jsonl")] == ["3", "4"]
    with pytest.raises(ValueError):
        parse_bulk(b"x", "roster.xlsx")


def test_partition_reports_rejects_by_row_and_catches_duplicates() -> None:
    rows = [
        {"external_id": "A1", "name": "Asha Rao", "degree_level": "UG", "specialization": "bsc-ai"},
        {"external_id": "A1", "name": "Asha Rao", "degree_level": "UG", "specialization": "bsc-ai"},
        {"external_id": "B2", "name": "B", "degree_level": "UG", "specialization": "bsc-ai"},
    ]
    accepted, rejects = partition(rows)
    assert [c.external_id for c in accepted] == ["A1"]
    assert [(r["row"], r["field"]) for r in rejects] == [(2, "external_id"), (3, "name")]


# ---------------------------------------------------------------------------
# SQS routing
# ---------------------------------------------------------------------------


def _candidate(i: int, degree: str = "UG") -> Candidate:
    return Candidate(f"C{i}", f"Cand {i}", degree, "bsc-ai")


def test_push_many_batches_in_tens_and_routes_by_degree() -> None:
    sqs = FakeSQS()
    queue = CandidateQueue(sqs, ug_url="ug-q", pg_url="pg-q")
    bodies = [queue_message(_candidate(i), source="test") for i in range(23)]
    sent, failed = queue.push_many("UG", bodies)
    assert len(sent) == 23 and failed == []
    assert len(sqs.sent["ug-q"]) == 23 and "pg-q" not in sqs.sent
    queue.push("pg", queue_message(_candidate(99, "PG"), source="test"))
    assert sqs.sent["pg-q"][0]["candidate"]["degree_level"] == "PG"


def test_partial_failure_is_reported_not_raised() -> None:
    sqs = FakeSQS(fail_ids={"C1"})
    queue = CandidateQueue(sqs, ug_url="ug-q")
    sent, failed = queue.push_many("UG", [queue_message(_candidate(i), source="t") for i in range(3)])
    assert len(sent) == 2 and len(failed) == 1 and failed[0]["Message"] == "boom"


def test_an_unconfigured_stream_is_refused_by_name() -> None:
    queue = CandidateQueue(FakeSQS(), ug_url="ug-q")
    assert queue.configured("UG") and not queue.configured("PG")
    with pytest.raises(QueueNotConfigured) as exc:
        queue.url_for("PG")
    assert "PLATFORM_PG_QUEUE_URL" in str(exc.value)


def test_pull_parses_bodies_and_ack_deletes() -> None:
    sqs = FakeSQS()
    queue = CandidateQueue(sqs, ug_url="ug-q")
    queue.push("UG", queue_message(_candidate(1), source="t"))
    messages = queue.pull("UG")
    assert len(messages) == 1 and messages[0].body["candidate"]["external_id"] == "C1"
    queue.ack("UG", messages[0].receipt_handle)
    assert sqs.deleted == [("ug-q", "h0")]


# ---------------------------------------------------------------------------
# the S3-trigger Lambda
# ---------------------------------------------------------------------------


def _event(bucket: str, key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key.replace(" ", "+")}}}]}


def test_lambda_validates_pushes_per_degree_and_writes_a_rejects_report() -> None:
    csv = (
        "usn,name,degree,specialization,email\n"
        "U1,Asha Rao,UG,BSc AI,asha@x.ac.in\n"
        "P1,Bhavana K,PG,MTech Data Science,\n"
        "bad row,No,UG,BSc AI,\n"
    ).encode()
    s3 = FakeS3({"uploads/batch 1.csv": csv})
    sqs = FakeSQS()
    queue = CandidateQueue(sqs, ug_url="ug-q", pg_url="pg-q")
    out = lambda_handler.handler(_event("bulk", "uploads/batch 1.csv"), s3=s3, queue=queue)
    summary = out["results"][0]
    assert summary["rows"] == 3 and summary["accepted"] == 2 and summary["rejected"] == 1
    assert summary["pushed"] == {"UG": 1, "PG": 1}
    assert sqs.sent["ug-q"][0]["source_ref"] == "s3://bulk/uploads/batch 1.csv"
    bucket, key, body = s3.puts[0]
    assert key == "rejects/uploads/batch 1.csv.rejects.json"
    assert json.loads(body)["rejects"][0]["field"] == "external_id"


def test_lambda_reports_a_stream_with_no_queue_instead_of_dropping_it() -> None:
    body = json.dumps([{"external_id": "P1", "name": "B K", "degree_level": "PG", "specialization": "x"}]).encode()
    s3 = FakeS3({"in.json": body})
    queue = CandidateQueue(FakeSQS(), ug_url="ug-q")  # no PG stream
    summary = lambda_handler.process_object("bulk", "in.json", body, queue=queue, s3=s3)
    assert summary["pushed"] == {} and summary["rejected"] == 1
    assert "PG stream" in json.loads(s3.puts[0][2])["rejects"][0]["error"]


def test_lambda_ignores_its_own_reports_and_survives_a_bad_file() -> None:
    s3 = FakeS3({"rejects/x.json": b"{}", "bad.csv": b"\xff\xfe"})
    queue = CandidateQueue(FakeSQS(), ug_url="ug-q")
    event = {"Records": _event("b", "rejects/x.json")["Records"] + _event("b", "bad.csv")["Records"]}
    out = lambda_handler.handler(event, s3=s3, queue=queue)
    assert out["processed"] == 1 and "error" in out["results"][0]
