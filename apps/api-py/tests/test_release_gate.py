"""tools/ci/release_gate.py decides what an agent may deploy without a human.

These tests are the contract. Loosening one is loosening what reaches
production unreviewed, so each refusal class is pinned by name. No database.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
_spec = importlib.util.spec_from_file_location("release_gate", REPO / "tools" / "ci" / "release_gate.py")
gate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# dataclasses resolve their module through sys.modules, so register it first.
sys.modules["release_gate"] = gate
_spec.loader.exec_module(gate)


def test_a_plain_web_change_ships_web_only() -> None:
    v = gate.classify(["apps/web/src/app/features/student/jobs/jobs.component.ts"])
    assert (v.target, v.auto_ok) == ("web-only", True)


def test_a_plain_api_change_ships_api_only() -> None:
    v = gate.classify(["apps/api-py/app/routers/jobs_feed.py", "apps/api-py/tests/test_jobs.py"])
    assert (v.target, v.auto_ok) == ("api-only", True)


def test_both_halves_ship_together() -> None:
    v = gate.classify(["apps/api-py/app/routers/swoc.py", "apps/web/src/app/app.routes.ts"])
    assert (v.target, v.auto_ok) == ("api-and-web", True)


def test_docs_and_tests_alone_deploy_nothing_and_are_never_auto() -> None:
    v = gate.classify(["docs/agentic-sdlc.md", "apps/api-py/tests/test_x.py", "AGENTS.md"])
    assert (v.target, v.auto_ok) == ("none", False)


def test_an_empty_change_set_is_not_a_release() -> None:
    v = gate.classify([])
    assert (v.target, v.auto_ok) == ("none", False)


def test_a_migration_needs_a_human() -> None:
    v = gate.classify(["apps/api-py/migrations/versions/abc_new.py", "apps/api-py/app/routers/swoc.py"])
    assert v.auto_ok is False and v.target == "api-only"


def test_a_model_change_needs_a_human() -> None:
    assert gate.classify(["apps/api-py/app/models/user.py"]).auto_ok is False


def test_infra_needs_a_human() -> None:
    assert gate.classify(["infra/cdk/reep_core/stack.py"]).auto_ok is False


def test_rule_1_rule_2_and_auth_files_need_a_human() -> None:
    for path in (
        "apps/api-py/app/ai/llm.py",
        "apps/api-py/app/routers/mentor.py",
        "apps/api-py/app/policies.py",
        "apps/api-py/app/security.py",
        "apps/api-py/app/config.py",
        "apps/api-py/app/purge_students.py",
    ):
        assert gate.classify([path]).auto_ok is False, path


def test_the_service_worker_and_dependencies_need_a_human() -> None:
    for path in (
        "apps/web/ngsw-config.json",
        "apps/web/public/reep-sw.js",
        "apps/web/src/app/app.config.ts",
        "apps/web/package.json",
        "apps/api-py/requirements.txt",
        "apps/api-py/Dockerfile",
    ):
        assert gate.classify([path]).auto_ok is False, path


def test_changing_the_deploy_pipeline_or_this_gate_needs_a_human() -> None:
    for path in (".github/workflows/deploy.yml", ".github/workflows/agent-release.yml", "tools/ci/release_gate.py"):
        assert gate.classify([path, "apps/web/src/main.ts"]).auto_ok is False, path


def test_an_unknown_path_is_refused_not_guessed() -> None:
    v = gate.classify(["somewhere/new/file.py", "apps/web/src/main.ts"])
    assert v.auto_ok is False
    assert any("not a path this gate knows" in r for r in v.reasons)


def test_a_big_release_needs_a_human() -> None:
    paths = [f"apps/web/src/app/f{i}.ts" for i in range(gate.MAX_FILES + 1)]
    assert gate.classify(paths).auto_ok is False


def test_every_refusal_says_why() -> None:
    v = gate.classify(["apps/api-py/migrations/versions/x.py", "infra/cdk/app.py"])
    assert len(v.reasons) == 2
