# Multi-stage image for the Teamflow OpenCode web runtime.
# Final command: opencode web --hostname 0.0.0.0 --port ${PORT:-3000}

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

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git curl python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY --from=builder /opt/uv-tools /opt/uv-tools
COPY --from=builder /opt/uv-python /opt/uv-python
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN groupadd --system --gid 1001 opencode \
    && useradd --system --uid 1001 --gid opencode --create-home opencode

ENV PATH="/opt/uv-tools/bin:${PATH}"

WORKDIR /app
COPY --chown=opencode:opencode . .

EXPOSE 3000

USER opencode

CMD ["sh", "-c", "opencode web --hostname 0.0.0.0 --port ${PORT:-3000}"]
