FROM python:3.14.7-slim-bookworm AS base

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419~deb12u2' \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder

ARG VERSION

ENV VIRTUAL_ENV=/opt/datamodel-code-generator
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt

LABEL maintainer="Koudai Aono <koxudaxi@gmail.com>"

RUN test -n "${VERSION}" \
    || { echo "The VERSION build arg is required." >&2; exit 1; } \
    && python -m venv "${VIRTUAL_ENV}" \
    && python -m pip install --no-cache-dir --upgrade 'pip>=26.2.2' \
    && python -m pip install --no-cache-dir "datamodel-code-generator[http]==${VERSION}" 'certifi>=2026.10.19'

FROM base

ARG VERSION

LABEL maintainer="Koudai Aono <koxudaxi@gmail.com>" \
    org.opencontainers.image.description="Generate Python data models from schema definitions" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/datamodel-code-generator/datamodel-code-generator" \
    org.opencontainers.image.title="datamodel-code-generator" \
    org.opencontainers.image.version="${VERSION}"

ENV VIRTUAL_ENV=/opt/datamodel-code-generator
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN useradd --create-home --shell /usr/sbin/nologin appuser
COPY --from=builder --chown=appuser:appuser /opt/datamodel-code-generator /opt/datamodel-code-generator
USER appuser

ENTRYPOINT ["datamodel-codegen"]
