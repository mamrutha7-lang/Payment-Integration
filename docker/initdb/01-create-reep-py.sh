#!/bin/bash
# Create the database the application actually uses.
#
# POSTGRES_DB creates `reep_dev`, but app/config.py defaults to `reep_py` — so a
# fresh `docker compose up -d` produced a server WITHOUT the app's database and
# the API failed with `FATAL: database "reep_py" does not exist`. AGENTS.md
# papered over it with a manual `docker exec reep-postgres createdb -U reep
# reep_py`, i.e. a setup step every new machine had to remember.
#
# Also creates the pgvector extension. `vector.control` is not marked
# `trusted = true`, so CREATE EXTENSION needs superuser — which is exactly what
# this script runs as, and is why the KB migration cannot create it itself when
# the app connects as an unprivileged role.
#
# CAVEAT: /docker-entrypoint-initdb.d runs ONLY when the data directory is
# empty. An existing reep_pgdata volume will not re-run this — that is Postgres
# behaviour, not a bug here. For an existing volume, run the same two statements
# by hand once.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE reep_py'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'reep_py')\gexec
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname reep_py <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

echo "initdb: reep_py ready (pgvector enabled)"
