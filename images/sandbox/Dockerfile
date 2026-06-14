FROM ghcr.io/astral-sh/uv:0.11.21-python3.12-alpine

RUN apk add --no-cache \
    fd \
    ripgrep \
    && UV_NO_CACHE=1 uv pip install --system 'pydantic>=2.0'

# Matchlock's SDK launches the image command before running exec requests.
# The upstream uv image defaults to `uv`, which exits with usage status when no
# args are provided, so keep the sandbox alive with a minimal idle command.
CMD ["sleep", "infinity"]
