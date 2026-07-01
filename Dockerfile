# syntax=docker/dockerfile:1.7

FROM python:3.14.6-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.17@sha256:03bdc89bb9798628846e60c3a9ad19006c8c3c724ccd2985a33145c039a0577b \
    /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable

FROM python:3.14.6-slim-trixie AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

RUN rm -f /etc/localtime \
    && cp /usr/share/zoneinfo/UTC /etc/localtime

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home app

USER 1000:1000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["enji-guard", "health", "--ready"]

ENTRYPOINT ["enji-guard"]
CMD ["run", "--transport", "streamable-http", "--host", "0.0.0.0"]
