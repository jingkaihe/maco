# maco sandbox image

This directory contains the Docker image used by the Docker and Matchlock `maco up` sandbox providers.

The image extends the pinned Alpine uv/Python image with the `maco` CLI, `ripgrep` (`rg`), and `fd`. Remote sandbox providers use the CLI to bootstrap `/workspace/macosdk/tools` from the live gateway catalog at sandbox startup.

## Build

```bash
VERSION="$(scripts/package-version)"
docker build -f images/sandbox/Dockerfile -t "ghcr.io/jingkaihe/maco:${VERSION}-alpine" .
```

## Import into Matchlock

```bash
VERSION="$(scripts/package-version)"
docker save "ghcr.io/jingkaihe/maco:${VERSION}-alpine" \
  | matchlock image import "ghcr.io/jingkaihe/maco:${VERSION}-alpine"
```
