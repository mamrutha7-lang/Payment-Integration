---
name: new-migration
description: Add an Alembic migration to apps/api-py correctly — one head, enum gotchas, indexes on the model, no fake backfills. Use whenever a model under apps/api-py/app/models/ changes.
---

# Adding a migration

1. **Change the model first** (`apps/api-py/app/models/`). A new module must be
   imported in `app/models/__init__.py` or autogenerate never sees it.
2. **Start from one head**: `cd apps/api-py && python -m alembic heads` must
   print exactly one row. Pull `main` first if not.
3. **Generate**: `python -m alembic revision --autogenerate -m "<what>"`, then
   read the file line by line — autogenerate is a draft.
4. **Fix the known traps** (AGENTS.md "Alembic enum gotchas"):
   - new enum column on an existing table → `CREATE TYPE` first;
   - new table reusing an existing enum → `postgresql.ENUM(..., name='x', create_type=False)`;
   - JSON columns are `JSONB`, never `JSON`;
   - every index the migration creates is also declared on the model (partial
     indexes with the same `postgresql_where`), or `alembic check` will ask to
     drop it forever;
   - a `server_default` goes on the model too.
5. **Backfills tell the truth.** Never stamp `now()` on history that happened
   earlier; NULL meaning "not recorded" is honest. Repair data before adding a
   unique constraint over it.
6. **Write a real `downgrade()`**, or raise with the reason if it cannot be
   exact.
7. **Verify** on your own database (never a shared one):
   `python -m alembic upgrade head && python -m alembic check && python -m alembic downgrade -1 && python -m alembic upgrade head`.
8. If the table holds people's data, give it a verdict in
   `app/purge_people.py` `VERDICTS` and `app/purge_students.py`
   `STUDENT_VERDICTS` — the tests refuse an unclassified table.
