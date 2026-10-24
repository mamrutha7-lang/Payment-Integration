"""The engine under the two console deletes: ONE row and everything that hangs
off it, decided by the foreign keys and by a written policy for every key that
does not decide for itself.

`app.purge_people` and `app.purge_students` empty TABLES: their verdicts are
whole-table, and that is the right shape for "hand this deployment over" and
"clear this cohort". It is the wrong shape for the Main Admin pressing Delete
on ONE student, ONE faculty account or ONE college, which is what this module
exists for (2026-09-16). Here the unit is a row, and the question is "which
other rows go with it" — a question the schema already answers for 148 of its
184 foreign keys with an ON DELETE clause. This module reads those clauses
and REFUSES TO GUESS about the rest.

HOW A ROW'S DEPENDANTS ARE FOUND. Start from the root row(s) — `users.id = ?`
for an account, `colleges.id = ?` for a college — and walk every foreign key
that points at a table already in the doomed set:

  * `ON DELETE CASCADE`   the child row goes too, and the walk continues
                          from it (a cascade's children cascade);
  * `ON DELETE SET NULL`  the child row STAYS and loses the pointer — the
                          record outlives the person, `redesign_audit_events`'
                          shape — and the walk stops there;
  * no clause, RESTRICT   the schema refuses to decide, so the POLICY must:
                          `DELETE_ROW`, `CLEAR_POINTER`, or `REFUSE_IF_ANY`
                          ("people are seated here; move them first"). A
                          column with no clause AND no policy entry aborts the
                          walk before anything is counted, let alone deleted —
                          the same "a table nobody classified aborts the run"
                          rule both purge modules keep, applied per column.

A policy may also OVERRIDE a clause the schema does carry. `import_rows.student_id`
is `SET NULL`, which keeps a spreadsheet line holding the student's USN, name
and marks after the student is gone; `account_deletion` classifies that edge
`DELETE_ROW` instead, and says why beside it.

THE PREDICATES ARE SUBQUERIES, NEVER MATERIALISED ID LISTS, and the rows are
deleted CHILDREN FIRST (`reversed(Base.metadata.sorted_tables)`, the order both
purge modules use). A child's predicate is `child.fk IN (SELECT parent.id WHERE
<parent predicate>)`, which is only true while the parent rows still exist —
so a parent is never deleted before the rows that reach through it have been.
Pointers are cleared before any row goes, for the same reason.

THE FILES ARE THE CALLER'S. This module knows rows; `account_deletion` reads
the stored names out of the doomed rows BEFORE `delete_rows` and destroys the
bytes first, which is `purge_people`'s "files go before rows" rule — a row is
the last pointer to a student's resume, and a delete that loses the pointer
first leaves bytes nobody can find.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.schema import ForeignKey

from .db import Base

# Importing the package registers every model on Base.metadata. The walk is
# only a guard if it runs against the WHOLE schema.
from . import models  # noqa: F401

log = logging.getLogger("reep.deletion_walk")

#: The three things a policy may say about a foreign-key column that points at
#: a table this walk deletes rows from.
DELETE_ROW = "delete"
CLEAR_POINTER = "clear"
REFUSE_IF_ANY = "refuse"

ACTIONS = frozenset({DELETE_ROW, CLEAR_POINTER, REFUSE_IF_ANY})


class DeletionRefused(RuntimeError):
    """Raised before anything is destroyed. Always a caller's or a reviewer's
    problem: an unclassified column, or people who must be moved first."""


def edge_key(fk: ForeignKey) -> tuple[str, str]:
    return (fk.parent.table.name, fk.parent.name)


def classify(fk: ForeignKey, policy: Mapping[tuple[str, str], str]) -> str | None:
    """What happens to the rows of `fk`'s table when its parent rows go.

    The policy wins over the schema, so an override is possible; then the
    schema's own clause; then nothing — which the walk refuses on.
    """
    key = edge_key(fk)
    if key in policy:
        action = policy[key]
        if action not in ACTIONS:
            raise DeletionRefused(f"{key[0]}.{key[1]} carries an unreadable policy {action!r}.")
        return action
    clause = (fk.ondelete or "").upper()
    if clause == "CASCADE":
        return DELETE_ROW
    if clause == "SET NULL":
        return CLEAR_POINTER
    return None


@dataclass
class Walk:
    """The closure of one delete: which tables lose rows, under what predicate,
    which pointers are cleared and which columns would refuse.

    Built once from the roots and the policy; the same object COUNTS (for the
    dry run / the dialog) and DELETES (for the real thing), so the two cannot
    disagree about what the delete reaches.
    """

    roots: dict[str, Any]
    policy: Mapping[tuple[str, str], str]
    #: table -> the FKs through which its rows are reached (DELETE_ROW edges).
    delete_edges: dict[str, list[ForeignKey]] = field(default_factory=dict)
    cleared_edges: list[ForeignKey] = field(default_factory=list)
    refusing_edges: list[ForeignKey] = field(default_factory=list)
    _memo: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.roots) - {t.name for t in Base.metadata.sorted_tables})
        if unknown:
            raise DeletionRefused(f"roots name tables the models do not declare: {unknown}")
        deleted = set(self.roots)
        seen: set[tuple[str, str]] = set()
        changed = True
        while changed:
            changed = False
            for table in Base.metadata.sorted_tables:
                for fk in table.foreign_keys:
                    parent = fk.column.table.name
                    if parent not in deleted:
                        continue
                    key = edge_key(fk)
                    if key in seen:
                        continue
                    action = classify(fk, self.policy)
                    if action is None:
                        raise DeletionRefused(
                            f"{key[0]}.{key[1]} points at {parent} with no ON DELETE clause "
                            "and no entry in this delete's policy. Say what happens to those "
                            "rows (DELETE_ROW, CLEAR_POINTER or REFUSE_IF_ANY) - this delete "
                            "will not guess, and nothing was changed."
                        )
                    seen.add(key)
                    if action == DELETE_ROW:
                        self.delete_edges.setdefault(table.name, []).append(fk)
                        if table.name not in deleted:
                            deleted.add(table.name)
                            changed = True
                    elif action == CLEAR_POINTER:
                        self.cleared_edges.append(fk)
                    else:
                        self.refusing_edges.append(fk)

    # ------------------------------------------------------------ predicates --

    @property
    def deleted_tables(self) -> list[str]:
        """Every table that loses rows, parents first."""
        names = set(self.roots) | set(self.delete_edges)
        return [t.name for t in Base.metadata.sorted_tables if t.name in names]

    def predicate(self, name: str, _stack: tuple[str, ...] = ()) -> Any:
        """The WHERE clause selecting `name`'s doomed rows, as subqueries over
        the parents' own predicates. Memoised; a cycle of DELETE edges (none
        exists today) is broken at the back edge rather than recursed into."""
        if name in self._memo:
            return self._memo[name]
        clauses = []
        if name in self.roots:
            clauses.append(self.roots[name])
        for fk in self.delete_edges.get(name, []):
            parent = fk.column.table.name
            if parent in _stack:
                log.warning("deletion walk: cycle at %s.%s, back edge ignored", name, fk.parent.name)
                continue
            clauses.append(
                fk.parent.in_(select(fk.column).where(self.predicate(parent, _stack + (name,))))
            )
        if not clauses:
            raise DeletionRefused(f"{name} is not reached by this delete.")
        expr = clauses[0] if len(clauses) == 1 else or_(*clauses)
        self._memo[name] = expr
        return expr

    def _pointer_clause(self, fk: ForeignKey) -> Any:
        return fk.parent.in_(select(fk.column).where(self.predicate(fk.column.table.name)))

    # ----------------------------------------------------------------- counts --

    def count_rows(self, db: Session) -> dict[str, int]:
        """table -> rows this delete removes. Zero-count tables are left out,
        so the report names only what actually goes."""
        out: dict[str, int] = {}
        for name in self.deleted_tables:
            table = Base.metadata.tables[name]
            n = db.scalar(select(func.count()).select_from(table).where(self.predicate(name)))
            if n:
                out[name] = int(n)
        return out

    def count_cleared(self, db: Session) -> dict[str, int]:
        """"table.column" -> rows that SURVIVE and lose their pointer.

        A row that is itself doomed is not counted here even where it also
        points at a doomed parent (an interview turn's `message_id`, a SWOC
        line's `linked_session_id`): it goes, and saying it "loses a pointer"
        would report one consequence as two."""
        out: dict[str, int] = {}
        doomed = set(self.deleted_tables)
        for fk in self.cleared_edges:
            where = [self._pointer_clause(fk)]
            if fk.parent.table.name in doomed:
                where.append(~self.predicate(fk.parent.table.name))
            n = db.scalar(select(func.count()).select_from(fk.parent.table).where(*where))
            if n:
                out[f"{fk.parent.table.name}.{fk.parent.name}"] = int(n)
        return out

    def count_refusals(self, db: Session) -> dict[str, int]:
        """"table.column" -> rows that would stop this delete."""
        out: dict[str, int] = {}
        for fk in self.refusing_edges:
            n = db.scalar(
                select(func.count()).select_from(fk.parent.table).where(self._pointer_clause(fk))
            )
            if n:
                out[f"{fk.parent.table.name}.{fk.parent.name}"] = int(n)
        return out

    def select_column(self, db: Session, name: str, column: str) -> list[Any]:
        """Non-null values of one column over the doomed rows of `name` — how a
        caller reads stored file names out before the rows go."""
        if name not in set(self.deleted_tables):
            return []
        col = Base.metadata.tables[name].c[column]
        stmt = select(col).where(self.predicate(name), col.is_not(None))
        return [value for (value,) in db.execute(stmt).all()]

    def select_rows(self, db: Session, name: str, *columns: str) -> list[tuple]:
        if name not in set(self.deleted_tables):
            return []
        table = Base.metadata.tables[name]
        return list(db.execute(select(*(table.c[c] for c in columns)).where(self.predicate(name))).all())

    # ----------------------------------------------------------------- writes --

    def clear_pointers(self, db: Session) -> dict[str, int]:
        """NULL every CLEAR_POINTER column on the rows that reach a doomed
        parent. Explicit even where the schema carries SET NULL, so the count
        the report printed is the count that was written. BEFORE any delete."""
        out: dict[str, int] = {}
        for fk in self.cleared_edges:
            result = db.execute(
                update(fk.parent.table)
                .where(self._pointer_clause(fk))
                .values({fk.parent.name: None})
            )
            if result.rowcount:
                out[f"{fk.parent.table.name}.{fk.parent.name}"] = int(result.rowcount)
        return out

    def delete_rows(self, db: Session) -> dict[str, int]:
        """Every doomed row, children first, roots last. NO COMMIT — the caller
        owns the transaction, which is what lets a test run the real delete
        against the real schema and roll it back."""
        out: dict[str, int] = {}
        doomed = set(self.deleted_tables)
        for table in reversed(Base.metadata.sorted_tables):
            if table.name not in doomed:
                continue
            result = db.execute(delete(table).where(self.predicate(table.name)))
            if result.rowcount:
                out[table.name] = int(result.rowcount)
        return out


def probe_policy(roots: list[str], policy: Mapping[tuple[str, str], str]) -> list[str]:
    """Build the walk with dummy root predicates and report what it refuses on.
    Empty means every edge the delete reaches is classified. For the guard
    tests and for the API's boot-time self-check, so a new foreign key lands
    as a failing test rather than as a 500 in the Main Admin's face."""
    dummy = {name: Base.metadata.tables[name].c.id == "-" for name in roots}
    try:
        Walk(roots=dummy, policy=policy)
    except DeletionRefused as exc:
        return [str(exc)]
    return []
