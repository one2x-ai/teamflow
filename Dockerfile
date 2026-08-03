# Multi-stage image for the Teamflow OpenCode web runtime.
# The container runs `opencode serve` directly (exec-form), binding loopback
# 127.0.0.1:13000 only, with cwd=/workspace/teamflow. A Caddy sidecar in the
# same Pod owns public port 3000 and reverse-proxies to this container.

FROM node:22-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git curl python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/opt/uv-tools/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy

RUN uv tool install basic-memory

RUN npm install --global opencode-ai@1.18.4 @earendil-works/pi-coding-agent

FROM node:22-slim AS runtime

# Configurable repository checkout (public repo, no credentials needed).
# Override TEAMFLOW_REPO_REF with --build-arg when a non-default ref is needed.
ARG TEAMFLOW_REPO_URL=https://github.com/one2x-ai/teamflow.git
ARG TEAMFLOW_REPO_REF=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git curl python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY --from=oven/bun:1.3.14 /usr/local/bin/bun /usr/local/bin/bun
COPY --from=builder /opt/uv-tools /opt/uv-tools
COPY --from=builder /opt/uv-python /opt/uv-python
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN groupadd --system --gid 1001 opencode \
    && useradd --system --uid 1001 --gid opencode --create-home opencode

ENV PATH="/opt/uv-tools/bin:${PATH}"

# Clone the public repository to /workspace/teamflow at the configured ref.
# The repo is public (visibility: public), so no authenticated BuildKit
# secret is required. The clone creates a non-bare checkout with a valid
# .git directory and full file tree.
RUN mkdir -p /workspace \
    && git clone --no-checkout "${TEAMFLOW_REPO_URL}" /workspace/teamflow \
    && git -C /workspace/teamflow checkout "${TEAMFLOW_REPO_REF}" \
    && chown -R opencode:opencode /workspace

WORKDIR /workspace/teamflow
EXPOSE 13000

USER opencode

CMD ["opencode", "serve", "--hostname", "127.0.0.1", "--port", "13000"]
