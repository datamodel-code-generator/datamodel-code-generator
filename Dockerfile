FROM python:3.14.8-slim-bookworm AS builder

ARG VERSION

ENV VIRTUAL_ENV=/opt/datamodel-code-generator
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

LABEL maintainer="Koudai Aono <koxudaxi@gmail.com>"

RUN test -n "${VERSION}" \
    || { echo "The VERSION build arg is required." >&2; exit 1; } \
    && python -m venv "${VIRTUAL_ENV}" \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --refresh-package=datamodel-code-generator "datamodel-code-generator[http]==${VERSION}"

FROM python:3.14.8-slim-bookworm

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
