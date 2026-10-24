"""The development-only MCP surface at `/mcp`.

WHAT IT IS FOR. While a screen is being built, the question "does this endpoint
actually return what the board draws?" is answered either by clicking through a
browser or by calling the endpoint directly. This mount makes the second one
available to a developer's tools: every GET under `/api` becomes a tool, the
caller's `reep_session` cookie is forwarded into the real handler, and the
answer comes back as the screen would receive it — through the same
`require_*` dependencies, with rule 2's narrowing applied. `python -m
app.dev_session` prints the cookie.

THREE THINGS KEEP IT FROM BEING A SECOND FRONT DOOR.

1. It is mounted only when `settings.mcp_enabled`, which is an ENV allowlist
   AND an explicit flag (app/config.py). An unrecognised ENV shuts it, like
   every other door in that file.

2. It is READ-ONLY BY CONSTRUCTION rather than by a list somebody maintains.
   The tools are derived from the app's own routes: GET, under `/api`, and
   nothing else. A POST cannot be reached by forgetting to exclude it.

   This is why `include_operations` is used rather than the `include_tags` the
   kit's draft suggested. In fastapi-mcp 0.4.0 the filters UNION rather than
   intersect: `include_tags=[...]` together with `exclude_operations=[...]`
   yields every operation on the app except the excluded ones, and the tag list
   narrows nothing. Every tag REEP's routers carry ("admin", "student",
   "mentor", "governance", "interview-records") contains write routes, so that
   pairing would have published `DELETE /api/admin/cohorts/{id}` as a tool.

3. `fastapi_mcp` is imported INSIDE the function, and is in requirements-dev.txt
   only. The Dockerfile installs requirements.txt, so the package is not in the
   image at all; CI's dependency-completeness job imports every module under
   `app/` against that same manifest, which passes because this import never
   runs with the flag off.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

log = logging.getLogger("reep.dev_mcp")

MOUNT_PATH = "/mcp"

#: The only method that becomes a tool. Read-only is the property this mount
#: relies on for its safety, so it is stated as a constant rather than inlined
#: into a condition somebody could widen without noticing what it cost.
READ_ONLY_METHOD = "GET"

#: The prefix the Angular client and the dev proxy forward. A router mounted
#: outside it is unreachable from the app anyway (see app/main.py's note on
#: interview_records), so it is not something a screen can be checked against.
API_PREFIX = "/api"


def read_only_operation_ids(app: FastAPI) -> list[str]:
    """The OpenAPI operation ids of every GET path under `/api`.

    READ FROM THE SCHEMA, not from `app.routes`. FastAPI 0.141 keeps included
    routers as nested `_IncludedRouter` objects rather than flattening their
    routes onto the application, so walking `app.routes` finds four
    documentation endpoints and nothing else — this returned an empty list
    before it read the schema, and the mount silently published no tools.

    The schema is also the source fastapi-mcp itself reads, so the ids here are
    exactly the ones its filter compares against. FastAPI generates an id for
    an operation that does not declare one, which is why this works before any
    router has been given an explicit `operation_id=`; as the later phases add
    them (stakeholder + action, per 09-coding-standards.md), the tool names
    simply get better.
    """
    method = READ_ONLY_METHOD.lower()
    operation_ids: list[str] = []
    for path, operations in app.openapi().get("paths", {}).items():
        if not path.startswith(API_PREFIX):
            continue
        operation = operations.get(method)
        if not isinstance(operation, dict):
            continue
        operation_id = operation.get("operationId")
        if operation_id:
            operation_ids.append(operation_id)
    return operation_ids


def mount_dev_mcp(app: FastAPI) -> bool:
    """Mount the MCP surface. Returns whether it was mounted.

    Wrapped whole: this is a development convenience, and a version skew
    between `fastapi-mcp` and `mcp` must cost the tool rather than the API. The
    two are pinned together in requirements-dev.txt precisely because the
    default resolution of `fastapi-mcp`'s own floor picks an `mcp` that raises
    here, and a developer meeting that should read one line rather than a
    startup traceback.
    """
    operation_ids = read_only_operation_ids(app)
    if not operation_ids:
        log.warning("Dev MCP: no GET routes under %s; not mounting.", API_PREFIX)
        return False

    try:
        # Lazy, and dev-only. See the module docstring's point 3.
        from fastapi_mcp import FastApiMCP

        surface = FastApiMCP(
            app,
            name="REEP dev API",
            description=(
                "Read-only access to this development server's API, as whoever "
                "holds the forwarded reep_session cookie."
            ),
            include_operations=operation_ids,
            # Forward the session cookie into the real handlers, so a tool call
            # is authenticated and scoped exactly as the browser would be.
            # NOTE this REPLACES the default ['authorization'] rather than
            # adding to it, which is what we want: REEP authenticates by cookie.
            headers=["cookie"],
        )
        surface.mount_http(mount_path=MOUNT_PATH)
    except Exception:
        log.exception(
            "Dev MCP: could not mount %s. The API is unaffected. Check that "
            "fastapi-mcp and mcp are both installed at the versions pinned in "
            "requirements-dev.txt — fastapi-mcp's own floor admits an mcp 2.x "
            "that removed the API it calls.",
            MOUNT_PATH,
        )
        return False

    log.info(
        "Dev MCP mounted at %s with %d read-only tools. Mint a cookie with "
        "`python -m app.dev_session --email <address>`.",
        MOUNT_PATH,
        len(operation_ids),
    )
    return True
