SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
.PHONY: help

run:
	@WORKERS=10 ADDRESS="localhost:52012" uv run python src/oqtopus_engine_estimator/app.py -c config/config.yaml -l config/logging.yaml

test:
	@uv run pytest

build :
	docker compose --env-file .env build

up :
	@if [ ! -d $(SHARE_DIR)/$(LOG_DIR) ]; then \
		echo ";; mkdir -p $(SHARE_DIR)/$(LOG_DIR)"; mkdir -p $(SHARE_DIR)/$(LOG_DIR); \
	fi
	docker compose --env-file .env up -d estimator

down :
	docker compose --env-file .env down estimator

ruff :
	@echo "...checking lint check"
	@uv run ruff check src
	@echo "...checking format check"
	@uv run ruff format src --diff --check

clean :
	@rm .venv -rf
	@rm .ruff_cache -rf
	@rm .pytest_cache -rf

help :
