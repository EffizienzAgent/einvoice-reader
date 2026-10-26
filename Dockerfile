FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
RUN useradd --uid 10001 --create-home app
COPY --from=builder /app/.venv /app/.venv
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
COPY licenses /app/licenses
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 WORKERS=1
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/v1/health')"
CMD ["sh", "-c", "exec uvicorn einvoice_reader.api:app --host 0.0.0.0 --port 8080 --workers ${WORKERS} --no-server-header"]
