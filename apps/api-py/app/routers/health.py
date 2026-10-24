"""Liveness and readiness probes.

Two endpoints, deliberately different:

  /health   LIVENESS  — is this process alive? Touches nothing, so it answers
                        even before Postgres is up. An orchestrator restarts a
                        container that fails this.
  /ready    READINESS — can this process actually serve requests? Checks the
                        dependencies a request needs. An orchestrator takes a
                        pod out of the load balancer when this fails, WITHOUT
                        killing it — the right response to a database blip.

Conflating the two is a common outage amplifier: if liveness checks the DB, a
brief Postgres wobble restarts every API container at once and turns a
recoverable dependency blip into a full outage.
"""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from ..config import settings
from ..db import SessionLocal

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Liveness. Intentionally dependency-free."""
    return {"status": "ok", "service": "reep-api-py"}


@router.get("/ready")
def ready(response: Response) -> dict:
    """Readiness — reports each dependency separately so a failing probe says
    WHICH one broke instead of just 'not ready'.

    Returns 503 when a hard dependency is down. Only hard dependencies fail the
    probe: a feature that degrades on its own and says so must not pull the
    whole API out of rotation."""
    checks: dict[str, object] = {}
    healthy = True

    # Hard dependency: every meaningful request reads Postgres.
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — the reason is the useful part
        checks["database"] = f"error: {type(exc).__name__}"
        healthy = False

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}
