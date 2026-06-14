VERSION := $(shell tr -d '[:space:]' < VERSION.txt)
IMAGE ?= ghcr.io/jingkaihe/maco:$(VERSION)-alpine

.PHONY: help sync lint type test test-unit test-integration check build clean image image-import release-tag

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
	  '  make image         Build sandbox image using VERSION.txt' \
	  '  make image-import  Build and import sandbox image into Matchlock' \
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

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache

image:
	docker build -f images/sandbox/Dockerfile -t "$(IMAGE)" .

image-import: image
	docker save "$(IMAGE)" | matchlock image import "$(IMAGE)"

release-tag:
	test -n "$(VERSION)"
	git tag "v$(VERSION)"
