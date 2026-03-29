SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

SHARE_DIR ?= .
LOG_DIR ?= logs

.PHONY: run, test
run:
	@WORKERS=10 ADDRESS="localhost:52013" uv run python src/oqtopus_engine_combiner/app.py -c config/config.yaml -l config/logging.yaml

build:
	@docker compose build

up:
	@if [ ! -d $(SHARE_DIR)/$(LOG_DIR) ]; then \
		echo ";; mkdir -p $(SHARE_DIR)/$(LOG_DIR)"; mkdir -p $(SHARE_DIR)/$(LOG_DIR); \
	fi
	docker compose up -d

down:
	docker compose down

test:
	@uv run pytest

lint:
	@uv run ruff check
