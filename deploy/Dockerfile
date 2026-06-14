# Secretary service image — built by deploy/docker-compose.yml.
# Build context is the repo root (see compose `context: ..`).
FROM python:3.12-slim

# fastembed/onnxruntime need libgomp at runtime; embeddings are CPU/ONNX (no torch).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1

# Lockfile-faithful install. Resolve from the pinned lock, with the console + mcp
# extras so one image can run the poll loop, the console, and the MCP server.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra console --extra mcp

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["secretary"]
CMD ["run"]
