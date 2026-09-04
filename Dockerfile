# MandateGuard Commerce Lab - public OFFLINE DEMO deployment image.
#
# The runtime installs no packages. Both halves of the product are Python
# standard library only:
#
#   * the authorization controller, and
#   * the discovery layer, which serves a 17k-listing catalog through frozen
#     binary indexes rather than a model runtime.
#
# scikit-learn and NumPy are needed to *build* the artifacts in data/models/
# (see requirements-train.txt) and are deliberately absent here. Live Test Mode
# additionally needs the `openai` package and server-side credentials, and is
# intentionally unavailable in this image.
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

# The frozen discovery catalog and its indexes: ~12 MB of committed artifacts,
# loaded once at startup. Absent them the server still starts and every
# authorization journey still works; the discovery surface reports why it is
# unavailable.
COPY data/processed/ /app/data/processed/
COPY data/models/ /app/data/models/

# Non-root execution with a writable scratch directory for the temporary
# SQLite semantic cache, execution ledger, and recovery audit.
RUN useradd --create-home --uid 10001 mandateguard \
    && mkdir -p "$TMPDIR" \
    && chown -R mandateguard:mandateguard "$TMPDIR" /app

USER mandateguard

EXPOSE 8080

CMD ["python", "scripts/run_commerce_lab.py"]
