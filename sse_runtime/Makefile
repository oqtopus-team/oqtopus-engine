SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

SSE_IMAGE := sse-v2/python3.13:latest

.PHONY: build-sse lint test
build-sse:
	docker build -t $(SSE_IMAGE) .

lint: ## Run linting
	@uv lock --check
	@uv run ruff check
	@uv run ruff format --check
	@uv run mypy

test: ## Run tests
	@uv run pytest
