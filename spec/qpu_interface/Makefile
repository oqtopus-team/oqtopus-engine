SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: generate
generate:
	buf generate --path oqtopus_engine_core/interfaces
