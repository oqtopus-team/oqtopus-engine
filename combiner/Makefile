SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

SHARE_DIR ?= .
LOG_DIR ?= logs

.PHONY: run, format, lint, test, verify, grpcurl-list, build, up, down, help

run: ## Run application
	@COMBINER_WORKERS=10 COMBINER_ADDRESS="localhost:51013" IDLE_QUBITS_INSERTION_ENABLED=false uv run python -m oqtopus_engine_combiner.app -c config/config.yaml -l config/logging.yaml

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

grpcurl-list: ## List gRPC services via grpcurl
	@grpcurl -plaintext localhost:51013 list

build: ## Build Docker image
	@docker compose build

up: ## Start Docker container
	@if [ ! -d $(SHARE_DIR)/$(LOG_DIR) ]; then \
		echo ";; mkdir -p $(SHARE_DIR)/$(LOG_DIR)"; mkdir -p $(SHARE_DIR)/$(LOG_DIR); \
	fi
	docker compose up -d

down: ## Stop Docker container
	docker compose down

help: ## Show help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(filter-out .env,$(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
