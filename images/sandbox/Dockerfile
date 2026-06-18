FROM ghcr.io/astral-sh/uv:0.11.21-python3.12-alpine

WORKDIR /build

RUN apk add --no-cache \
    fd \
    ripgrep

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN UV_NO_CACHE=1 uv pip install --system . \
    && addgroup -g 1000 -S maco \
    && adduser -u 1000 -S -D -G maco -h /workspace maco \
    && mkdir -p /workspace \
    && chown -R 1000:1000 /workspace \
    && rm -rf /build

WORKDIR /workspace
USER 1000:1000

# Matchlock's SDK launches the image command before running exec requests.
# The upstream uv image defaults to `uv`, which exits with usage status when no
# args are provided, so keep the sandbox alive with a minimal idle command.
CMD ["sleep", "infinity"]
