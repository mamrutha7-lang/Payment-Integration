"""tools/agent/claude_settings.py -- the issue agent's only door into .claude/settings.json.

Pinned because the door is narrow on purpose: it must change the permission
lists and nothing else (the attribution keys keep the owner the only commit
author), and it must never leave the file unparseable.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("claude_settings", ROOT / "tools/agent/claude_settings.py")
claude_settings = importlib.util.module_from_spec(_spec)
sys.modules["claude_settings"] = claude_settings
_spec.loader.exec_module(claude_settings)

BASE = {
    "permissions": {"allow": ["Bash(git status*)", "Bash(git log*)", "Bash(npx ng build*)"]},
    "includeCoAuthoredBy": False,
    "attribution": {"commit": "", "pr": ""},
}


@pytest.fixture
def settings(tmp_path: Path) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(BASE, indent=2) + "\n")
    return path


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def test_add_after_places_the_entry_and_leaves_every_other_key_alone(settings):
    claude_settings.main(["add", "allow", "Bash(git show*)", "--after", "Bash(git log*)"], path=settings)
    data = read(settings)
    assert data["permissions"]["allow"] == [
        "Bash(git status*)", "Bash(git log*)", "Bash(git show*)", "Bash(npx ng build*)",
    ]
    assert data["includeCoAuthoredBy"] is False
    assert data["attribution"] == {"commit": "", "pr": ""}


def test_adding_an_existing_entry_changes_nothing(settings):
    before = settings.read_text()
    claude_settings.main(["add", "allow", "Bash(git log*)"], path=settings)
    assert settings.read_text() == before


def test_remove_takes_out_exactly_one_entry(settings):
    claude_settings.main(["remove", "allow", "Bash(git status*)"], path=settings)
    assert read(settings)["permissions"]["allow"] == ["Bash(git log*)", "Bash(npx ng build*)"]


def test_removing_a_missing_entry_is_refused(settings):
    with pytest.raises(SystemExit):
        claude_settings.main(["remove", "allow", "Bash(terraform apply)"], path=settings)


def test_an_unknown_after_anchor_is_refused_and_the_file_is_untouched(settings):
    before = settings.read_text()
    with pytest.raises(SystemExit):
        claude_settings.main(["add", "allow", "Bash(x)", "--after", "Bash(nope)"], path=settings)
    assert settings.read_text() == before


def test_only_the_permission_lists_are_reachable(settings):
    with pytest.raises(SystemExit):
        claude_settings.main(["add", "attribution", "x"], path=settings)


def test_a_file_that_is_not_json_is_refused_rather_than_overwritten(settings):
    settings.write_text("{ not json")
    with pytest.raises(SystemExit):
        claude_settings.main(["add", "allow", "Bash(x)"], path=settings)
    assert settings.read_text() == "{ not json"
