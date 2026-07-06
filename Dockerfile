# syntax=docker/dockerfile:1.7

FROM python:3.14.6-slim-trixie@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.17@sha256:c8e5089d066253e105538cd1d77ad4c124631bfcb7ed918f25b2ee1b8b0903fb \
    /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

ARG PACKAGE_VERSION=0.0.0+local
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${PACKAGE_VERSION} \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ENJI_GUARD_CLI=${PACKAGE_VERSION}

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-build --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable

FROM python:3.14.6-slim-trixie@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS runtime

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
CMD ["run"]
