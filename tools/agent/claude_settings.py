"""tools/agent/claude_settings.py -- the one way the issue agent edits .claude/settings.json.

Claude Code protects its own settings files: an Edit or Write on
.claude/settings.json is refused in an unattended run even when an allow rule
names the path (issue #112). The owner has allowed the agent to change its own
permissions, so this script is that door, and it is deliberately narrow:

  * it touches permissions.allow and permissions.deny and nothing else, so the
    attribution keys (includeCoAuthoredBy, attribution) that keep the owner the
    only commit author can never be edited through it;
  * it reads the file as JSON, refuses if it does not parse, and writes it back
    as JSON, so a run cannot leave the file broken;
  * every change still lands on a branch and reaches main only through a PR
    the owner merges.

Usage:
    python3 tools/agent/claude_settings.py add allow "Bash(git show*)" [--after "Bash(git log*)"]
    python3 tools/agent/claude_settings.py remove allow "Bash(terraform apply)"
    python3 tools/agent/claude_settings.py list allow
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SETTINGS = Path(__file__).resolve().parents[2] / ".claude" / "settings.json"
LISTS = ("allow", "deny")


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"refused: {path} is not readable JSON ({exc})")
    if not isinstance(data, dict):
        sys.exit(f"refused: {path} is not a JSON object")
    return data


def rules(data: dict, which: str) -> list:
    perms = data.setdefault("permissions", {})
    entries = perms.setdefault(which, [])
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        sys.exit(f"refused: permissions.{which} is not a list of strings")
    return entries


def main(argv: list[str] | None = None, path: Path = SETTINGS) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("add", "remove", "list"))
    parser.add_argument("which", choices=LISTS)
    parser.add_argument("entry", nargs="?")
    parser.add_argument("--after", help="insert after this existing entry")
    args = parser.parse_args(argv)

    data = load(path)
    entries = rules(data, args.which)

    if args.action == "list":
        print("\n".join(entries))
        return 0
    if not args.entry or not args.entry.strip():
        sys.exit("refused: name the entry to add or remove")
    entry = args.entry.strip()

    if args.action == "add":
        if entry in entries:
            print(f"unchanged: {entry!r} is already in permissions.{args.which}")
            return 0
        if args.after is not None:
            if args.after not in entries:
                sys.exit(f"refused: --after {args.after!r} is not in permissions.{args.which}")
            entries.insert(entries.index(args.after) + 1, entry)
        else:
            entries.append(entry)
    else:
        if entry not in entries:
            sys.exit(f"refused: {entry!r} is not in permissions.{args.which}")
        entries.remove(entry)

    path.write_text(json.dumps(data, indent=2) + "\n")
    load(path)  # prove the written file parses
    print(f"{args.action}ed: {entry!r} in permissions.{args.which}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
