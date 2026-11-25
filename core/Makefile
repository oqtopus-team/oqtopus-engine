SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: install run run-sse run-slurm-simulator format lint test test-slurm-process verify help

install: ## Install dependencies and make the SLURM scripts executable
	@uv sync --all-groups
	@chmod +x ../deployment/slurm/run_qulacs_mpi.py ../deployment/slurm/run_qulacs_mpi_job.sh
	@chmod +x ../test-infra/scheduler/slurm/host-bridge.sh ../test-infra/scheduler/slurm/run_qulacs_mpi_job.sh ../test-infra/scheduler/slurm/smoke.sh ../test-infra/scheduler/slurm/host-cli/*

run: ## Run application
	@uv run python -m oqtopus_engine_core.app -c config/config.yaml -l config/logging.yaml

run-sse: ## Run application with SSE engine configuration
	@uv run python -m oqtopus_engine_core.app -c config/sse_engine_config.yaml -l config/sse_engine_logging.yaml

run-slurm-simulator: ## Run application with SLURM simulator configuration
	@uv run python -m oqtopus_engine_core.app -c config/slurm_simulator_config.yaml -l config/logging.yaml

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

test-slurm-process: ## Run the fake-SLURM process integration tests
	@uv run pytest -q tests/slurm_process_e2e/test_fake_slurm_process.py

verify: format lint test ## Run all verification steps (formatting, linting, testing)

help: ## Show help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(filter-out .env,$(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
