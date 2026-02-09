SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: run run-sse test lint
run:
	@uv run python src/oqtopus_engine_core/app.py -c config/config.yaml -l config/logging.yaml

run-sse:
	@uv run python src/oqtopus_engine_core/app.py -c config/sse_engine_config.yaml -l config/sse_engine_logging.yaml

test:
	@uv run pytest

lint:
	@uv run ruff check
