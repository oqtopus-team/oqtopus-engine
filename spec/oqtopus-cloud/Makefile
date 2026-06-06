SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: download-oas
download-oas: ## Download the OQTOPUS Cloud Provider API OpenAPI Specification (OAS) from main branch
	curl https://raw.githubusercontent.com/oqtopus-team/oqtopus-cloud/main/backend/oas/provider/openapi.yaml -o openapi.yaml

.PHONY: download-oas-dev
download-oas-dev: ## Download the OQTOPUS Cloud Provider API OpenAPI Specification (OAS) from develop branch
	curl https://raw.githubusercontent.com/oqtopus-team/oqtopus-cloud/develop/backend/oas/provider/openapi.yaml -o openapi.yaml

.PHONY: generate
generate: ## Generate the OQTOPUS Cloud Provider API client code using Swagger Codegen
	java -jar swagger-codegen-cli-3.0.68.jar generate -l python -i openapi.yaml -o output -c swagger-codegen-config.json && \
	rm -rf ../../core/src/oqtopus_engine_core/interfaces/oqtopus_cloud && \
	cp -pr output/oqtopus_engine_core ../../core/src
