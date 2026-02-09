SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: run, test
run:
	@WORKERS=10 ADDRESS="localhost:52013" uv run python src/oqtopus_engine_combiner/app.py -c config/config.yaml -l config/logging.yaml

test:
	@uv run pytest

lint:
	@uv run ruff check
