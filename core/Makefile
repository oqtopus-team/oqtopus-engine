SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: run run-sse format lint test verify help

run: ## Run application
	@uv run python -m oqtopus_engine_core.app -c config/config.yaml -l config/logging.yaml

run-sse: ## Run application with SSE engine configuration
	@uv run python -m oqtopus_engine_core.app -c config/sse_engine_config.yaml -l config/sse_engine_logging.yaml

format: ## Run code formatting
	@uv run ruff check --fix
	@uv run ruff format

lint: ## Run linting
	@uv lock --check
	@uv run ruff check
	@uv run ruff format --check
	@uv run mypy

test: ## Run tests
	@uv run pytest

verify: format lint test ## Run all verification steps (formatting, linting, testing)

help: ## Show help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(filter-out .env,$(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
