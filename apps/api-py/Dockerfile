# REEP API — FastAPI on Python 3.14.
#
# Separate from the voice worker on purpose. The two share almost no
# dependencies (API ~370 MB, dominated by litellm and google-adk; worker ~505 MB,
# dominated by livekit and onnxruntime), they scale differently, and merging them
# would ship ~800 MB of site-packages to both a stateless web pod and a
# long-lived audio pod.
#
# glibc base, not Alpine: psycopg has musl wheels but the shared toolchain and
# ca-certificates story is simpler here, and libpq — unlike the worker's TLS —
# has no certifi fallback to lean on.

FROM python:3.14-slim-bookworm

# PYTHONUNBUFFERED so logs reach the orchestrator immediately rather than
# sitting in a pipe buffer during an incident.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# pg_dump 17, for the nightly logical backup (app/backup_database.py).
#
# NOT the distribution's client, and the major version is not a detail. Debian
# bookworm ships PostgreSQL 16's client, the database is 17, and pg_dump REFUSES
# a server newer than itself. That refusal is the good failure -- it stops
# rather than writing something subtly wrong -- but it stops at 01:00 with
# nobody watching, and the thing that does not get written is the only
# engine-portable copy of every student record. So the major is pinned here,
# from PostgreSQL's own repository, and `tests/test_codebase_guards.py` compares
# this number against `engine_version` in `infra/cdk/reep_core/stack.py`. They
# are ONE FACT WRITTEN IN TWO FILES: an RDS upgrade that forgets this line does
# not break the API, it breaks only the backup, silently.
#
# ~20 MB in a web-facing image, and worth saying why that is acceptable rather
# than leaving the next reader to wonder. This task already holds DATABASE_URL
# and psycopg, so it can already read every row; pg_dump adds convenience for an
# attacker who has already won, not access. What it buys is that the backup runs
# on the api's OWN task definition -- one image, one ECR repository, one deploy
# path, and the scheduler role's existing ecs:RunTask grant on `reep-api:*`. A
# second image for one nightly job is a second thing to build, scan, and forget
# to rebuild.
#
# `apt-mark manual ca-certificates` before the purge is deliberate: it is a
# dependency of curl here, and --auto-remove would take it with curl on a base
# image where it had been marked automatic. An api that cannot verify TLS
# reaches no AWS endpoint at all, and the failure would be at runtime.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    apt-mark manual ca-certificates; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-17; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*; \
    pg_dump --version

# Dependencies first so a code change does not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# Non-root. The API writes nothing to disk except uploads, which belong on a
# mounted volume or object storage rather than the container filesystem.
RUN groupadd --gid 10001 reep \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin reep \
 && mkdir -p /var/reep/uploads \
 && chown -R 10001:10001 /app /var/reep
USER 10001:10001

# UPLOAD_DIR must be a MOUNTED volume in production. Left blank, config.py
# resolves to /app/var/uploads INSIDE the container layer, so every redeploy
# silently destroys student uploads. This ENV only relocates it somewhere
# mountable — it is not a substitute for actually mounting it.
ENV UPLOAD_DIR=/var/reep/uploads

EXPOSE 3300

# --host 0.0.0.0 is REQUIRED in a container. Locally uvicorn is started with
# only --port, so it binds 127.0.0.1 and is unreachable from outside the
# network namespace — the container would look healthy and refuse every request.
#
# Migrations are deliberately NOT run here: this entrypoint runs in every
# replica, and concurrent `alembic upgrade head` across replicas races on the
# version table. Run them once, as a separate job/init container.
#   --host 0.0.0.0            uvicorn defaults to 127.0.0.1, which in a container
#                             binds the container's own loopback and refuses
#                             every probe while looking perfectly healthy.
#   --proxy-headers           behind a TLS-terminating ingress, so the app sees
#                             the real scheme/client rather than the proxy's.
#                             WHICH peers to believe comes from the
#                             FORWARDED_ALLOW_IPS env var below — uvicorn reads
#                             it natively when the CLI flag is absent. It was
#                             "*", and "*" is not a default, it is a decision:
#                             trusting X-Forwarded-For from ANY peer lets a
#                             client prepend fake entries and pick its own
#                             request.client.host — which is the key the
#                             registration rate limiter buckets by, so one
#                             machine rotating a header gets an unlimited
#                             budget on the app's only unauthenticated write.
#                             The default is this deployment's VPC
#                             (infra/aws/network.tf cidr_block): every real
#                             proxy hop — ALB, the nginx `web` container — is
#                             inside it, every client is not, so uvicorn
#                             resolves the rightmost address a proxy actually
#                             appended and ignores whatever the client typed.
#                             Override per environment; never set it back to *.
#   --timeout-graceful-shutdown 110
#                             strictly BELOW the 120s stop_grace_period, so
#                             uvicorn finishes draining before SIGKILL. This
#                             matters for /api/agent/chat/stream, which persists
#                             the assistant turn only after the last delta — a
#                             hard kill mid-stream loses the turn.
#   --ws-per-message-deflate false
#                             the mock interview's socket carries 24 kHz PCM
#                             both ways, 25 frames a second each, and PCM barely
#                             compresses. With deflate negotiated (browsers
#                             offer it by default) every one of those frames was
#                             zlib-compressed or -decompressed on the one event
#                             loop that also relays the interviewer's voice, on
#                             half a vCPU -- work that bought nothing and
#                             competed with the downlink the student hears.
#   --no-access-log           uvicorn's OWN access log prints the full URL,
#                             query string included -- the Google sign-in
#                             callback's one-time `code` and `state` among
#                             them -- and duplicated every request in a second
#                             format. `reep.access` (app/traceability.py) already
#                             writes one line per request with method, path,
#                             status, duration and request id, and deliberately
#                             no query string, body or cookie (2026-10-13).
# NEVER --reload (AGENTS.md: it wedges a stale worker on this platform).
ENV FORWARDED_ALLOW_IPS="10.42.0.0/16"

# The commit this image was built from, and nothing else. Declared here, after
# every COPY and RUN, so a changed value invalidates only these two trivial
# layers and never the pip install. deploy.yml passes --build-arg GIT_SHA from
# the workflow's commit, and app/observability.py hands it to Sentry as the
# release — the same string the SPA reports and the same string source maps
# are uploaded under, so "first seen in this release" reads across both halves.
# Blank is a supported value: an empty SENTRY_RELEASE is falsy, no release is
# reported, and nothing else changes. The task definition sets no
# SENTRY_RELEASE of its own, so this ENV is what the container runs with.
ARG GIT_SHA=""
ENV SENTRY_RELEASE=$GIT_SHA
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "3300", \
     "--proxy-headers", \
     "--timeout-graceful-shutdown", "110", \
     "--ws-per-message-deflate", "false", \
     "--no-access-log"]
