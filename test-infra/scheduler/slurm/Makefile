SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

SLURM_RUNTIME_IMAGE ?= oqtopus-slurm-qulacs:0.6.14-amd64

export DOCKER_DEFAULT_PLATFORM := linux/amd64
export SLURM_RUNTIME_IMAGE

COMPOSE := docker compose -f compose.yaml

.PHONY: build up down down-volumes restart config smoke up-host down-host restart-host config-host help

build: ## Build the MPI-Qulacs scheduler image
	@$(COMPOSE) build slurmctld

up: build ## Start the Docker SLURM cluster
	@$(COMPOSE) up -d --wait

down: ## Stop the Docker SLURM cluster
	@$(COMPOSE) down

down-volumes: ## Stop the cluster and remove its named volumes
	@$(COMPOSE) down --volumes

restart: down up ## Restart the Docker SLURM cluster

config: ## Render the regular Docker Compose configuration
	@$(COMPOSE) config

smoke: up ## Run the two-node MPI-Qulacs smoke test
	@bash ./smoke.sh

up-host: build ## Start the cluster with host-visible scheduler paths
	@bash ./host-bridge.sh up

down-host: ## Stop the host-visible scheduler cluster
	@bash ./host-bridge.sh down

restart-host: down-host up-host ## Restart the host-visible scheduler cluster

config-host: ## Render the host bridge Compose configuration
	@bash ./host-bridge.sh config

help: ## Show help message
	@echo "Usage: make [target]"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(filter-out .env,$(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'
