# Track 1 — General-Purpose AI Agent (linux/amd64).
# Tiny, fast-starting image: python-slim + openai client only. No model weights,
# no baked secrets — the harness injects FIREWORKS_* and ALLOWED_MODELS at runtime.
#
# Build (from Apple Silicon, add --platform linux/amd64):
#   docker buildx build --platform linux/amd64 -t <registry>/nova-track1:latest --push .
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# The harness mounts /input and /output; create them so a bare run still works.
RUN mkdir -p /input /output

ENTRYPOINT ["python", "agent.py"]
