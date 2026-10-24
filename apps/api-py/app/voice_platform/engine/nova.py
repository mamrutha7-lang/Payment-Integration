"""Compile the per-degree catalogue into the engine's own contract and run the
existing Nova 2 Sonic session with it.

The architecture's AI Interview Engine is Nova Sonic 2, and REEP already has
one: `app.interview_nova.NovaSonicSession`, with the persona, the phase
machine, the deterministic answer gate, the two-beat close and the scorecard
tool call all pinned by tests. This module does not copy any of it. It builds
an `interview_matrix.Specialization` from the platform's rows — persona,
frameworks, syllabus, voice and the question bank — and passes the degree
level's time limit as the session cap. Everything downstream (the router's
writers, the recorder hook, the three close layers, the Angular client) is the
same code path as `/api/interview`.

Rule 1 holds: the compiled instructions are staff-authored catalogue text. No
candidate record is read here and no student field is composed into them.

WHICH CATALOGUE (B5.4). This module reads the PLATFORM's tables —
`platform_specializations` / `platform_questions`, keyed on `(degree_level,
key)`. The dashboard's `/api/interview` reads the OTHER one,
`interview_tracks` / `interview_bank_questions`, through
`app/interview_tracks.resolve_specialization`. The two were deliberately NOT
merged in Phase 4c: what they duplicate is the storage, not the interviewer —
both compile to `interview_matrix.Specialization` and run the same session — and
the schemas disagree on the two things that matter (`degree_level` keying here,
the institutional spine there; a per-question `rubric` here, none there).
`docs/voice-platform.md` carries the table and the shape any future merge should
take.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket
from sqlalchemy.orm import Session

from ...config import settings
from ...interview_matrix import Specialization
from ...models.voice_platform import PlatformQuestion, PlatformSpecialization
from ..storage import aurora

log = logging.getLogger("app.voice_platform.engine")

#: Bedrock closes a bidirectional stream after this many seconds, whatever
#: the catalogue asks for. A limit above it is capped, with a log line.
_BEDROCK_STREAM_WALL_S = 480

_GENERIC_SAMPLE_QUESTION = (
    "Walk me through a project or piece of coursework you are proud of, and "
    "what you would do differently now."
)


@dataclass(frozen=True)
class EngineConfig:
    degree_level: str
    specialization: Specialization
    specialization_id: str
    max_seconds: int
    wrap_up_reserve_seconds: int
    question_count: int


def compile_specialization(
    spec: PlatformSpecialization, questions: list[PlatformQuestion]
) -> Specialization:
    """Catalogue rows → the matrix's dataclass. The first PROBING question
    doubles as `sample_question` (the field the fixed rows use), and the whole
    active bank becomes `question_bank` in phase order."""
    active = [q for q in questions if q.active]
    probing = [q for q in active if q.phase == "probing"]
    sample = (probing[0].text if probing else active[0].text if active else _GENERIC_SAMPLE_QUESTION)
    return Specialization(
        key=spec.key,
        label=f"{spec.label} ({spec.degree_level})",
        persona=spec.persona,
        frameworks=tuple(str(f) for f in (spec.frameworks or ())),
        sample_question=sample,
        nova_voice=spec.nova_voice or "",
        syllabus=tuple(str(s) for s in (spec.syllabus or ())),
        question_bank=tuple(f"[{q.phase}] {q.text}" for q in active),
    )


def load_engine_config(db: Session, degree_level: str, specialization_key: str) -> EngineConfig | None:
    """Everything the bridge needs for one call, or None for an unknown or
    inactive specialization (the bridge closes 4010)."""
    spec = aurora.find_specialization(db, degree_level, specialization_key)
    if spec is None or not spec.active:
        return None
    questions = aurora.list_questions(db, spec.id, active_only=True)
    max_seconds, reserve = aurora.effective_time_limit(db, spec.degree_level, spec.id)
    if max_seconds > _BEDROCK_STREAM_WALL_S:
        log.warning(
            "Time limit for %s/%s is %ds, above Bedrock's %ds stream wall; the "
            "session will be capped there",
            spec.degree_level,
            spec.key,
            max_seconds,
            _BEDROCK_STREAM_WALL_S,
        )
    return EngineConfig(
        degree_level=spec.degree_level,
        specialization=compile_specialization(spec, questions),
        specialization_id=spec.id,
        max_seconds=int(max_seconds),
        wrap_up_reserve_seconds=int(reserve),
        question_count=len(questions),
    )


def engine_ready() -> bool:
    """The platform speaks Nova Sonic 2 and only that: INTERVIEW_ENGINE=local
    has no Bedrock stream to bridge to."""
    return settings.interview_engine.strip().lower() != "local" and settings.nova_sonic_ready


def build_session(
    websocket: WebSocket,
    conn_id: str,
    config: EngineConfig,
    *,
    recorder: Any | None,
    on_turn: Callable[..., None] | None,
    on_report: Callable[..., None] | None,
    on_finalize: Callable[..., None] | None,
    on_heartbeat: Callable[[], None] | None,
) -> Any:
    """The engine, lazily imported (it pulls the smithy stack in)."""
    from ...interview_nova import NovaSonicSession

    return NovaSonicSession(
        websocket,
        conn_id,
        on_turn=on_turn,
        specialization=config.specialization,
        on_report=on_report,
        on_finalize=on_finalize,
        on_heartbeat=on_heartbeat,
        recorder=recorder,
        max_seconds=config.max_seconds,
    )
