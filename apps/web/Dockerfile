# REEP web — the Angular SPA compiled once, served by nginx.
#
# Before this image existed NOTHING served the SPA in production: the only
# serving path was `ng serve` behind proxy.conf.json, which is a development
# server. Two stages so the node toolchain never ships: the first produces
# dist/web/browser, the second is nginx plus static files and one config file.

# ---- Stage 1: build ----------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# Dependencies first, so a source edit does not invalidate the npm layer —
# the same reasoning as apps/api-py/Dockerfile's pip layer.
COPY package.json package-lock.json ./
# `npm ci`, never `npm install`: reproduce the lockfile exactly or fail loudly,
# rather than quietly resolving something newer than what was tested.
RUN npm ci

# Explicit COPYs, not `COPY . .`: the build context on a dev machine contains
# the host's node_modules (native binaries for the wrong platform) and a stale
# dist/, and either one landing in this stage breaks or poisons the build.
# .dockerignore keeps them out of the context; this keeps them out of the image
# even if that file drifts.
COPY angular.json tsconfig.json tsconfig.app.json ./
COPY public ./public
COPY src ./src

# angular.json's defaultConfiguration is production: hashed filenames
# (outputHashing: all — which nginx.conf's immutable-cache rule relies on) and
# the bundle budgets enforced, the same build CI runs. The application builder
# writes to dist/<project>/browser and the project is named "web", so the
# output is dist/web/browser.
RUN npx ng build

# angular.json's production configuration emits HIDDEN source maps beside every
# script — no sourceMappingURL comment, but a predictable `<chunk>.js.map` next
# to each chunk, with `sourcesContent` inlined — so that deploy.yml can upload
# them to Sentry and then DELETE them before publishing to S3. This image is
# the self-hosted path and has no upload step, so the maps must not survive the
# build stage: nginx.conf's `try_files $uri` would serve every one, and with
# sourcesContent they ARE the application's source. The `test` turns a silent
# failure of the delete into a failed build rather than a served map.
RUN find dist/web/browser -name '*.map' -delete \
 && test "$(find dist/web/browser -name '*.map' | wc -l)" = "0"

# ---- Stage 2: serve ----------------------------------------------------------
# Stock nginx:alpine rather than the nginxinc/nginx-unprivileged variant: the
# master process keeps root only to bind :80/:443 and supervise, while every
# worker that touches a request runs as the unprivileged `nginx` user. The
# unprivileged image is the drop-in swap if the platform mandates no root at
# all — note it listens on 8080, so the compose port mapping and the TLS block
# in nginx.conf move with it.
FROM nginx:alpine

# Replace the stock default.conf rather than adding beside it, so there is
# exactly ONE :80 server and no "welcome to nginx" page shadowing routes.
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist/web/browser /usr/share/nginx/html

EXPOSE 80
