VERSION := $(shell tr -d '[:space:]' < VERSION.txt)
IMAGE ?= ghcr.io/jingkaihe/maco:$(VERSION)-alpine

.PHONY: help sync lint type test test-unit test-integration check build build-release clean clean-sandboxes clean-docker-sandboxes clean-matchlock-sandboxes image image-import version build-info-reset release-tag

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
	  '  make clean-sandboxes Remove leaked maco Docker/Matchlock sandboxes' \
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

clean-sandboxes: clean-docker-sandboxes clean-matchlock-sandboxes

clean-docker-sandboxes:
	@if command -v docker >/dev/null 2>&1; then \
		ids="$$(docker ps -aq --filter label=maco.managed=true 2>/dev/null || true)"; \
		if [ -n "$$ids" ]; then docker rm -f $$ids; fi; \
	fi

clean-matchlock-sandboxes:
	@if command -v matchlock >/dev/null 2>&1; then \
		running_ids="$$(matchlock list 2>/dev/null | awk 'NR > 1 && $$3 ~ /(^|\/)maco:/ && $$2 == "running" {print $$1}')"; \
		if [ -n "$$running_ids" ]; then for id in $$running_ids; do matchlock kill $$id 2>/dev/null || true; done; fi; \
		ids="$$(matchlock list 2>/dev/null | awk 'NR > 1 && $$3 ~ /(^|\/)maco:/ && $$2 != "running" {print $$1}')"; \
		if [ -n "$$ids" ]; then for id in $$ids; do matchlock rm $$id 2>/dev/null || true; done; fi; \
		matchlock gc 2>/dev/null || true; \
		if [ -n "$$ids" ]; then for id in $$ids; do matchlock rm $$id 2>/dev/null || true; done; fi; \
	fi

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
