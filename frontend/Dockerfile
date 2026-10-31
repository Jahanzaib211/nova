# Frontend Dockerfile
# Supports two targets:
#   --target dev   — install deps only, run `pnpm dev` at container start
#   --target prod  — full build baked in, run `pnpm start` at container start (default if no --target is specified)

ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store
ARG NPM_REGISTRY

# ── Base: shared setup ────────────────────────────────────────────────────────
FROM node:22-alpine AS base
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
# Configure corepack registry before installing pnpm so the download itself
# succeeds in restricted networks (COREPACK_NPM_REGISTRY controls where
# corepack fetches package managers from).
# Corepack caches the package manager it downloads under COREPACK_HOME, which
# defaults to the *invoking user's* home. These RUN steps execute as root, so
# without an explicit path pnpm lands in /root/.cache — and the prod stage runs
# as `node`, which looks in /home/node/.cache, misses, and re-downloads pnpm
# from the npm registry on **every fresh container**. Observed live: six failed
# starts in thirty seconds on a flaky link before one download finally
# succeeded, which reads as a crash loop and trips the drift gate's
# restart_storm check.
#
# A shared, world-readable COREPACK_HOME makes the baked install the one that is
# actually found, whichever user runs it.
ENV COREPACK_HOME=/opt/corepack
RUN if [ -n "${NPM_REGISTRY}" ]; then \
      export COREPACK_NPM_REGISTRY="${NPM_REGISTRY}"; \
    fi && \
    corepack enable && corepack install -g pnpm@10.26.2 && \
    chmod -R a+rX "${COREPACK_HOME}"
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
# Optionally override npm registry for restricted networks (e.g. NPM_REGISTRY=https://registry.npmmirror.com)
RUN if [ -n "${NPM_REGISTRY}" ]; then pnpm config set registry "${NPM_REGISTRY}"; fi
WORKDIR /app
COPY frontend ./frontend

# ── Dev: install only, CMD is overridden by docker-compose ───────────────────
FROM base AS dev
RUN cd /app/frontend && pnpm install --frozen-lockfile
# Run as non-root, matching the prod stage. docker-compose-dev.yaml already
# claims "the dev target runs as USER node already; no cap_add needed" — that
# was untrue: this stage had no USER, so the dev image ran as root, and the
# image in use only ran as `node` because it had been built from the prod stage
# instead. The chown is required, not cosmetic: `pnpm install` runs as root, and
# `next dev` has to write `.next/` into this tree.
RUN chown -R node:node /app/frontend
USER node
EXPOSE 3000

# ── Builder: install + compile Next.js ───────────────────────────────────────
FROM base AS builder
ARG DEER_FLOW_INTERNAL_GATEWAY_BASE_URL
ENV DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=${DEER_FLOW_INTERNAL_GATEWAY_BASE_URL}
RUN cd /app/frontend && pnpm install --frozen-lockfile
# Validate env at build (audit B7). The schema (src/env.js) marks all
# NEXT_PUBLIC_* / server vars optional because runtime routing is handled by
# nginx, so validation passes cleanly here while still catching an invalid
# value or a future required var instead of silently baking a misconfig.
RUN cd /app/frontend && pnpm build

# ── Prod: minimal runtime with pre-built output ───────────────────────────────
FROM node:22-alpine AS prod
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
# Corepack caches the package manager it downloads under COREPACK_HOME, which
# defaults to the *invoking user's* home. These RUN steps execute as root, so
# without an explicit path pnpm lands in /root/.cache — and the prod stage runs
# as `node`, which looks in /home/node/.cache, misses, and re-downloads pnpm
# from the npm registry on **every fresh container**. Observed live: six failed
# starts in thirty seconds on a flaky link before one download finally
# succeeded, which reads as a crash loop and trips the drift gate's
# restart_storm check.
#
# A shared, world-readable COREPACK_HOME makes the baked install the one that is
# actually found, whichever user runs it.
ENV COREPACK_HOME=/opt/corepack
RUN if [ -n "${NPM_REGISTRY}" ]; then \
      export COREPACK_NPM_REGISTRY="${NPM_REGISTRY}"; \
    fi && \
    corepack enable && corepack install -g pnpm@10.26.2 && \
    chmod -R a+rX "${COREPACK_HOME}"
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
RUN if [ -n "${NPM_REGISTRY}" ]; then pnpm config set registry "${NPM_REGISTRY}"; fi
WORKDIR /app

# v7.2 Batch 2A: non-root user.
# node:22-alpine ships a `node` user (uid 1000); we use it instead of
# creating a new one to keep the image minimal.
COPY --from=builder --chown=node:node /app/frontend ./frontend
USER node
EXPOSE 3000
CMD ["sh", "-c", "cd /app/frontend && pnpm start"]
