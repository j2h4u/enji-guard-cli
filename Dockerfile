# syntax=docker/dockerfile:1.7

FROM python:3.14.6-slim-trixie@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.17@sha256:c8e5089d066253e105538cd1d77ad4c124631bfcb7ed918f25b2ee1b8b0903fb \
    /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

ARG PACKAGE_VERSION
ARG SOURCE_COMMIT
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${PACKAGE_VERSION} \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ENJI_GUARD_CLI=${PACKAGE_VERSION}

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-build --no-install-project --no-dev

COPY src ./src
RUN PACKAGE_VERSION="${PACKAGE_VERSION}" SOURCE_COMMIT="${SOURCE_COMMIT}" python - <<'PY'
import os
import re
from pathlib import Path

package_version = os.environ["PACKAGE_VERSION"]
commit = os.environ["SOURCE_COMMIT"]
if re.fullmatch(r"(?!0\.0\.0)(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[A-Za-z0-9.+-]+)?", package_version) is None:
    raise SystemExit("PACKAGE_VERSION must be a non-0.0.0 semantic version")
if re.fullmatch(r"[0-9a-fA-F]{7,40}", commit) is None:
    raise SystemExit("SOURCE_COMMIT must be a Git object id")
Path("src/enji_guard_cli/_build_provenance.py").write_text(
    f'"""Build-time source provenance."""\n\nCOMMIT_SHA = "{commit.lower()}"\n',
    encoding="utf-8",
)
PY
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable

FROM python:3.14.6-slim-trixie@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS runtime

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
