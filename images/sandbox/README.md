# maco sandbox image

This directory contains the Docker image used by the Docker and Matchlock `maco serve-mcp` sandbox providers.

The image extends the pinned Alpine uv/Python image with the `maco` CLI, `ripgrep` (`rg`), and `fd`. Remote sandbox providers use the CLI to bootstrap `/workspace/macosdk/tools` from the live gateway catalog at sandbox startup.

## Build

```bash
docker build -f images/sandbox/Dockerfile -t ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine .
```

## Import into Matchlock

```bash
docker save ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine \
  | matchlock image import ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine
```
