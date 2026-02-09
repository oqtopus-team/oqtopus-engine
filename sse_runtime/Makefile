SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

SSE_IMAGE := sse-v2/python3.13:latest

.PHONY: build-sse
build-sse:
	docker build -t $(SSE_IMAGE) .
