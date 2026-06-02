SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: run, format, lint, test, verify, grpcurl-list, build, up, down, help

run: ## Run application
	@ESTIMATOR_WORKERS=10 ESTIMATOR_ADDRESS="localhost:51012" uv run python -m oqtopus_engine_estimator.app -c config/config.yaml -l config/logging.yaml

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
	@grpcurl -plaintext localhost:51012 list

build: ## Build Docker image
	docker compose --env-file .env build

up: ## Start Docker container
	@if [ ! -d $(SHARE_DIR)/$(LOG_DIR) ]; then \
		echo ";; mkdir -p $(SHARE_DIR)/$(LOG_DIR)"; mkdir -p $(SHARE_DIR)/$(LOG_DIR); \
	fi
	docker compose --env-file .env up -d estimator

down: ## Stop Docker container
	docker compose --env-file .env down estimator

help: ## Show help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(filter-out .env,$(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
