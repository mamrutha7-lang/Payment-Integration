"""Candidate-record validation for the bulk ingest (S3 CSV/JSON → Lambda → SQS).

STANDARD LIBRARY ONLY. This module is zipped into the Lambda unchanged (see
the package docstring), and it is also what the API's own bulk endpoint and
the drain worker validate with — one validator, three callers, so a record
the Lambda accepts is a record the worker can store.

A record is a flat mapping. Column names are matched case-insensitively
against a small alias table so the placement office's spreadsheet does not
have to be renamed to be accepted: `USN`, `Roll No` and `candidate_id` all
mean `external_id`. Whatever else the row carries is ignored, not rejected.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

DEGREE_LEVELS: frozenset[str] = frozenset({"UG", "PG"})

_DEGREE_ALIASES: dict[str, str] = {
    "ug": "UG",
    "undergraduate": "UG",
    "under graduate": "UG",
    "under-graduate": "UG",
    "bachelor": "UG",
    "bachelors": "UG",
    "bsc": "UG",
    "btech": "UG",
    "b.tech": "UG",
    "bba": "UG",
    "bca": "UG",
    "pg": "PG",
    "postgraduate": "PG",
    "post graduate": "PG",
    "post-graduate": "PG",
    "master": "PG",
    "masters": "PG",
    "msc": "PG",
    "mtech": "PG",
    "m.tech": "PG",
    "mba": "PG",
    "mca": "PG",
}

#: canonical field → accepted column names (lower-case, trimmed).
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "external_id": ("external_id", "usn", "id", "candidate_id", "roll_no", "roll no", "roll number", "application_id", "reg_no"),
    "name": ("name", "full_name", "full name", "candidate_name", "student_name"),
    "email": ("email", "e-mail", "mail", "email_address"),
    "degree_level": ("degree_level", "degree", "level", "degree level", "programme_level", "program_level"),
    "specialization": ("specialization", "specialisation", "track", "stream", "course", "branch"),
    "programme": ("programme", "program", "course_name", "degree_name"),
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_SPEC_KEY_RE = re.compile(r"[^a-z0-9]+")


class CandidateValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


@dataclass(frozen=True)
class Candidate:
    external_id: str
    name: str
    degree_level: str
    specialization: str
    email: str | None = None
    programme: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_degree(value: Any) -> str:
    """'Undergraduate', 'ug', 'B.Tech' → 'UG'; 'MTech', 'pg' → 'PG'. Raises."""
    text = str(value or "").strip().lower()
    if not text:
        raise CandidateValidationError("degree_level", "is required (UG or PG)")
    if text.upper() in DEGREE_LEVELS:
        return text.upper()
    mapped = _DEGREE_ALIASES.get(text)
    if mapped:
        return mapped
    raise CandidateValidationError(
        "degree_level", f"{value!r} is not a recognised degree level (UG or PG)"
    )


def specialization_key(value: Any) -> str:
    """'BSc AI' → 'bsc-ai', 'MTech Data Science' → 'mtech-data-science'. The
    same slug the catalogue's `key` column stores."""
    slug = _SPEC_KEY_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return slug


def _pick(record: Mapping[str, Any], field: str) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in record.items()}
    for alias in _FIELD_ALIASES[field]:
        if alias in lowered and lowered[alias] not in (None, ""):
            return lowered[alias]
    return None


def validate_candidate(
    record: Mapping[str, Any],
    *,
    allowed_specializations: Mapping[str, Iterable[str]] | None = None,
) -> Candidate:
    """One row → a `Candidate`, or `CandidateValidationError` naming the field.

    `allowed_specializations` maps a degree level to the specialization keys
    its catalogue actually has; when given, a key outside it is rejected so an
    unknown track is caught at upload rather than at the socket (close 4010).
    """
    if not isinstance(record, Mapping):
        raise CandidateValidationError("record", "must be an object")

    raw_id = _pick(record, "external_id")
    if raw_id is None:
        raise CandidateValidationError("external_id", "is required")
    external_id = str(raw_id).strip()
    if not _EXTERNAL_ID_RE.match(external_id):
        raise CandidateValidationError(
            "external_id", f"{external_id!r} must be 1-64 letters, digits, '.', '_' or '-'"
        )

    raw_name = _pick(record, "name")
    name = " ".join(str(raw_name or "").split())
    if len(name) < 2:
        raise CandidateValidationError("name", "is required")
    if len(name) > 160:
        raise CandidateValidationError("name", "is longer than 160 characters")

    degree = normalize_degree(_pick(record, "degree_level"))

    raw_spec = _pick(record, "specialization")
    spec = specialization_key(raw_spec)
    if not spec:
        raise CandidateValidationError("specialization", "is required")
    if allowed_specializations is not None:
        allowed = {specialization_key(s) for s in allowed_specializations.get(degree, ())}
        if spec not in allowed:
            raise CandidateValidationError(
                "specialization",
                f"{raw_spec!r} is not in the {degree} catalogue"
                + (f" ({', '.join(sorted(allowed))})" if allowed else " (which is empty)"),
            )

    email: str | None = None
    raw_email = _pick(record, "email")
    if raw_email is not None:
        email = str(raw_email).strip().lower()
        if not _EMAIL_RE.match(email) or len(email) > 320:
            raise CandidateValidationError("email", f"{email!r} is not an email address")

    programme = _pick(record, "programme")
    programme = str(programme).strip()[:120] if programme is not None else None

    return Candidate(
        external_id=external_id,
        name=name,
        degree_level=degree,
        specialization=spec,
        email=email,
        programme=programme or None,
    )


def parse_bulk(payload: bytes, filename: str) -> list[dict[str, Any]]:
    """CSV, JSON (a list, or `{"candidates": [...]}`) or JSON Lines → rows."""
    lowered = filename.lower()
    text = payload.decode("utf-8-sig")
    if lowered.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader if any((v or "").strip() for v in row.values() if isinstance(v, str))]
    if lowered.endswith(".jsonl") or lowered.endswith(".ndjson"):
        rows = []
        for line in text.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
    if lowered.endswith(".json"):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("candidates") or data.get("records") or data.get("rows") or []
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of records or an object with a 'candidates' list")
        return [r for r in data if isinstance(r, dict)]
    raise ValueError(f"unsupported upload type: {filename!r} (use .csv, .json or .jsonl)")


def partition(
    rows: Iterable[Mapping[str, Any]],
    *,
    allowed_specializations: Mapping[str, Iterable[str]] | None = None,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Split rows into accepted candidates and rejects `{row, field, error}`.
    Duplicate external ids within one upload are rejected on their second
    appearance rather than silently collapsed."""
    accepted: list[Candidate] = []
    rejects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        try:
            candidate = validate_candidate(row, allowed_specializations=allowed_specializations)
        except CandidateValidationError as exc:
            rejects.append({"row": index, "field": exc.field, "error": exc.message})
            continue
        if candidate.external_id.lower() in seen:
            rejects.append({"row": index, "field": "external_id", "error": f"duplicate of an earlier row ({candidate.external_id})"})
            continue
        seen.add(candidate.external_id.lower())
        accepted.append(candidate)
    return accepted, rejects


def queue_message(candidate: Candidate, *, source: str, source_ref: str | None = None) -> dict[str, Any]:
    """The JSON body pushed onto the degree level's queue."""
    return {
        "type": "candidate",
        "version": 1,
        "source": source,
        "source_ref": source_ref,
        "candidate": candidate.as_dict(),
    }
