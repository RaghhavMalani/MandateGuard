# MandateGuard Commerce Lab - public OFFLINE DEMO deployment image.
#
# The offline demo path is Python standard library only, so the runtime image
# installs no packages. Live Test Mode additionally needs the `openai` package
# and server-side credentials, and is intentionally unavailable in this image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    TMPDIR=/var/tmp/mandateguard

WORKDIR /app

# Only the files the running server actually reads.
COPY src/ /app/src/
COPY fixtures/agentic_commerce/ /app/fixtures/agentic_commerce/
COPY fixtures/recovery/ /app/fixtures/recovery/
COPY scripts/run_commerce_lab.py /app/scripts/run_commerce_lab.py

# Non-root execution with a writable scratch directory for the temporary
# SQLite semantic cache, execution ledger, and recovery audit.
RUN useradd --create-home --uid 10001 mandateguard \
    && mkdir -p "$TMPDIR" \
    && chown -R mandateguard:mandateguard "$TMPDIR" /app

USER mandateguard

EXPOSE 8080

CMD ["python", "scripts/run_commerce_lab.py"]
