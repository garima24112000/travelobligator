# The backup image for AWS Lambda (arm64), used by scripts/backup/aws/template.json. Build from the repository root:
#
#     docker build --platform linux/arm64 -f scripts/backup/aws/Dockerfile -t travelobligator-backup:local .
#
# It holds what .github/workflows/neon-backup.yml installed on GitHub's runner, and nothing else:
#   pg_dump / pg_restore 16   postgresql-client-16 from the PostgreSQL project's signed archive (Neon runs 16)
#   GnuPG >= 2.3              Debian trixie's gnupg (decrypting the AES-256-OCB format needs 2.3 or later)
#   AWS CLI                   Debian's awscli (the backup script calls `aws s3api put-object --if-none-match`)
#   the Lambda runtime client and boto3, pinned by hash
# plus the UNCHANGED scripts/backup/neon_s3_backup.py and the handler, in the repository's layout.
ARG PYTHON_IMAGE=python:3.11-slim-trixie
FROM ${PYTHON_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN set -eux; \
    apt-get update -qq; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg awscli; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update -qq; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    apt-get purge -y curl; apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb; \
    /usr/lib/postgresql/16/bin/pg_dump --version; gpg --version | head -n 1; aws --version

COPY scripts/backup/aws/requirements.txt /opt/backup/aws/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /opt/backup/aws/requirements.txt

COPY scripts/backup/neon_s3_backup.py /opt/backup/neon_s3_backup.py
COPY scripts/backup/aws/lambda_handler.py /opt/backup/aws/lambda_handler.py

# Lambda's file system is read-only except /tmp; the handler and the script keep all work there.
ENV PG_BIN_DIR=/usr/lib/postgresql/16/bin \
    HOME=/tmp \
    TMPDIR=/tmp
USER 10001:10001
WORKDIR /opt/backup/aws
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["lambda_handler.handler"]
