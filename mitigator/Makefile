SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
.PHONY: help

run:
	@WORKERS=10 ADDRESS="localhost:52011" uv run python src/oqtopus_engine_mitigator/app.py -c config/config.yaml -l config/logging.yaml

test:
	@uv run pytest

build :
	docker compose --env-file .env build

up :
	@if [ ! -d $(SHARE_DIR)/$(LOG_DIR) ]; then \
		echo ";; mkdir -p $(SHARE_DIR)/$(LOG_DIR)"; mkdir -p $(SHARE_DIR)/$(LOG_DIR); \
	fi
	docker compose --env-file .env up -d mitigator

down :
	docker compose --env-file .env down mitigator

ruff :
	@echo "...checking lint check"
	@uv run ruff check src/mitigator.py
	@echo "...checking format check"
	@uv run ruff format src/mitigator.py --diff --check

mypy:
	@echo "...checking type check"
	@uv run mypy src/mitigator.py

clean :
	@rm .venv -rf
	@rm .ruff_cache -rf
	@rm .pytest_cache -rf

help :
