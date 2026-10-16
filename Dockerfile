# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS builder
# git: market-sources and claude-subprocess come from git ([tool.uv.sources]).
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: a change in src/ does not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-editable --no-install-project
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
# Playwright's Node driver (~130 MB) is useless without Chromium; only the browser target gets it back.
RUN mv /app/.venv/lib/python3.12/site-packages/playwright/driver /opt/playwright-driver

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS runtime
ARG VERSION=0.1.3
LABEL org.opencontainers.image.source="https://github.com/octrow/buy-smarter" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /home/app/.cache/buysmarter \
    && chown -R app:app /home/app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
# Migrations run as a separate `alembic upgrade head`, not on start.
COPY alembic.ini ./
COPY alembic ./alembic
USER 10001:10001
VOLUME /home/app/.cache/buysmarter
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health').status != 200)"]
CMD ["uvicorn", "buysmarter.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Headless Chromium for Ozon (Playwright); Kaspi and OLX do not need it.
# --only-shell: the headed browser serves only the interactive `ozon-setup`, which has no display here.
FROM runtime AS browser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY --from=builder /opt/playwright-driver /app/.venv/lib/python3.12/site-packages/playwright/driver
USER 0
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001
