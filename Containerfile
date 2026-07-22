# syntax=docker/dockerfile:1.7

FROM python:3.14-slim-bookworm AS python-builder

ARG UV_VERSION=0.8.15

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/stands-engine/.venv

WORKDIR /src

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock README.md ./
COPY App ./App
COPY config ./config
COPY InfraBaseLib ./InfraBaseLib
COPY ManifestParser ./ManifestParser
COPY ShellCollect ./ShellCollect
COPY StandBuilder ./StandBuilder
COPY StandFramework ./StandFramework
COPY main.py ./main.py

RUN uv sync --locked --no-dev --no-editable


FROM python:3.14-slim-bookworm AS runtime

ARG TARGETARCH
ARG PULUMI_VERSION=3.245.0
ARG PULUMI_HCLOUD_VERSION=1.38.0

ENV PATH="/opt/stands-engine/.venv/bin:/usr/local/bin:${PATH}" \
    PULUMI_HOME=/tmp/.pulumi \
    PULUMI_SKIP_UPDATE_CHECK=true \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && case "${TARGETARCH}" in \
         amd64) pulumi_arch=x64 ;; \
         arm64) pulumi_arch=arm64 ;; \
         *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl --fail --location --silent --show-error \
         "https://get.pulumi.com/releases/sdk/pulumi-v${PULUMI_VERSION}-linux-${pulumi_arch}.tar.gz" \
         --output /tmp/pulumi.tar.gz \
    && tar -xzf /tmp/pulumi.tar.gz -C /tmp \
    && mv /tmp/pulumi/pulumi /tmp/pulumi/pulumi-* /usr/local/bin/ \
    && rm -rf /tmp/pulumi /tmp/pulumi.tar.gz

RUN mkdir -p /opt/pulumi \
    && PULUMI_HOME=/opt/pulumi pulumi plugin install resource hcloud "${PULUMI_HCLOUD_VERSION}" \
    && chmod -R a+rX /opt/pulumi

RUN groupadd --gid 10001 stands-engine \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin stands-engine \
    && install -d -o 10001 -g 10001 /data /workspace \
    && install -d -m 1777 /tmp/.pulumi

WORKDIR /opt/stands-engine

COPY --from=python-builder /src /opt/stands-engine
COPY --from=python-builder /opt/stands-engine/.venv /opt/stands-engine/.venv
COPY container/entrypoint.sh /usr/local/bin/stands-engine-entrypoint

RUN chmod 0755 /usr/local/bin/stands-engine-entrypoint \
    && chown -R 10001:10001 /opt/stands-engine /data /tmp/.pulumi

USER 10001:10001
WORKDIR /workspace

ENTRYPOINT ["stands-engine-entrypoint"]
CMD ["--help"]
