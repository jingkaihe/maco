# maco sandbox image

This directory contains the Docker image used by the Docker and Matchlock
`maco serve-mcp` sandbox providers.

The image is intentionally small: it extends the pinned Alpine uv/Python image
with `pydantic`, `ripgrep` (`rg`), and `fd` so generated wrappers and wrapper
discovery work without requiring package installation at sandbox runtime.

## Build

```bash
docker build -t ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine images/sandbox
```

## Import into Matchlock

```bash
docker save ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine \
  | matchlock image import ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine
```
