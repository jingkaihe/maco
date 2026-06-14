VERSION := $(shell tr -d '[:space:]' < VERSION.txt)
IMAGE ?= ghcr.io/jingkaihe/maco:$(VERSION)-alpine

.PHONY: help sync lint type test test-unit test-integration check build build-release clean image image-import version build-info-reset release-tag

help:
	@printf '%s\n' \
	  'Targets:' \
	  '  make sync          Install/sync Python dependencies' \
	  '  make lint          Run ruff checks' \
	  '  make type          Run ty checks' \
	  '  make test          Run all tests' \
	  '  make test-unit     Run unit tests' \
	  '  make test-integration  Run integration tests' \
	  '  make check         Run lint, type, and tests' \
	  '  make build         Build Python sdist/wheel' \
	  '  make build-release Build wheel with embedded commit/date metadata' \
	  '  make image         Build sandbox image using VERSION.txt' \
	  '  make image-import  Build and import sandbox image into Matchlock' \
	  '  make version       Print maco version metadata' \
	  '  make build-info-reset Reset local build metadata to unreleased defaults' \
	  '  make release-tag   Create a local v$$(cat VERSION.txt) git tag' \
	  '  make clean         Remove local build/test artifacts'

sync:
	uv sync --all-groups

lint:
	uv run ruff check src tests

type:
	uv run ty check src tests

test:
	uv run pytest -q

test-unit:
	uv run pytest -q tests/unit

test-integration:
	uv run pytest -q tests/integration

check: lint type test

build:
	uv build

build-release:
	python scripts/write-build-info
	uv build
	python scripts/write-build-info --reset

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache

image:
	docker build -f images/sandbox/Dockerfile -t "$(IMAGE)" .

image-import: image
	docker save "$(IMAGE)" | matchlock image import "$(IMAGE)"

version:
	uv run maco version

build-info-reset:
	python scripts/write-build-info --reset

release-tag:
	test -n "$(VERSION)"
	git tag "v$(VERSION)"
