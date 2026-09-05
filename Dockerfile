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

# Dataset attribution. The normalized catalog is a CC BY-SA 4.0 derivative, so
# the licence and provenance travel with the bytes rather than living only in
# the repository.
COPY data/provenance/ /app/data/provenance/

# The measured reports SYSTEM SCALE and MODEL QUALITY render. Without these the
# interface advertises measurements the container cannot load, which is worse
# than showing no numbers at all. .dockerignore allowlists exactly these three
# files out of artifacts/, plus data/models/training_report.json above.
COPY artifacts/engineering/discovery/ /app/artifacts/engineering/discovery/

# The authorization-scale claim is loaded from the measured primary rung and
# cross-checked against the world frozen before execution. Both inputs must be
# present or the interface says UNAVAILABLE.
COPY artifacts/engineering/authorization-scale/benchmark.json /app/artifacts/engineering/authorization-scale/benchmark.json
COPY data/eval/authorization-scale/WORLD_FREEZE.json /app/data/eval/authorization-scale/WORLD_FREEZE.json

# The measured Playground outcome mix. The sandbox catalogue itself is generated
# deterministically in-process on first use, so no catalogue artifact is copied
# and none needs to be: only this report, which SCALE LAB renders.
COPY data/eval/judge-playground/JUDGE_QUERY_REPORT.json /app/data/eval/judge-playground/

# Non-root execution with a writable scratch directory for the temporary
# SQLite semantic cache, execution ledger, and recovery audit.
RUN useradd --create-home --uid 10001 mandateguard \
    && mkdir -p "$TMPDIR" \
    && chown -R mandateguard:mandateguard "$TMPDIR" /app

USER mandateguard

EXPOSE 8080

CMD ["python", "scripts/run_commerce_lab.py"]
