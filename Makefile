.PHONY: help docker-build docker-run docker-stop docker-clean docker-logs docker-push compose-up compose-down compose-logs

IMAGE ?= gaia:latest

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── Single container ───────────────────────────────────────────────

docker-build: ## Build the container image
	podman build -t $(IMAGE) . || docker build -t $(IMAGE) .

docker-run: ## Run the TUI (single container)
	podman run -it --rm \
		--network=host \
		-w /home/gaia \
		-e LLM_ENDPOINT=$(LLM_ENDPOINT) \
		-e GAIA_HOME=$(HOME)/.gaia \
		-v $(HOME)/.gaia:/home/gaia/data \
		-e "COLUMNS=$(shell tput cols 2>/dev/null || echo 80)" \
		-e "LINES=$(shell tput lines 2>/dev/null || echo 24)" \
		$(IMAGE) || \
	docker run -it --rm \
		--add-host=host.docker.internal:host-gateway \
		-w /home/gaia \
		-e LLM_ENDPOINT=$(LLM_ENDPOINT) \
		-e GAIA_HOME=$(HOME)/.gaia \
		-v $(HOME)/.gaia:/home/gaia/data \
		-e "COLUMNS=$(shell tput cols 2>/dev/null || echo 80)" \
		-e "LINES=$(shell tput lines 2>/dev/null || echo 24)" \
		$(IMAGE)

docker-stop: ## Stop a running container by name
	podman stop gaia-tui 2>/dev/null || docker stop gaia-tui 2>/dev/null || true

docker-clean: ## Remove the image and dangling layers
	podman rmi $(IMAGE) 2>/dev/null || docker rmi $(IMAGE) 2>/dev/null || true
	podman image prune -f 2>/dev/null || docker image prune -f

docker-logs: ## Tail logs from a named container
	podman logs -f gaia-tui 2>/dev/null || docker logs -f gaia-tui 2>/dev/null || echo "No container named 'gaia-tui' found."

docker-push: ## Push to a registry (set REGISTRY env var)
	@:$(if $(REGISTRY),,$(error Set REGISTRY=<registry>/<name>))
	podman tag $(IMAGE) $(REGISTRY) && podman push $(REGISTRY) || \
	docker tag $(IMAGE) $(REGISTRY) && docker push $(REGISTRY)

# ── Compose (multi-container: Gaia + ChromaDB) ─────────────────────

COMPOSE_CMD := $(shell command -v podman 2>/dev/null && echo "podman compose" || echo "docker compose")

compose-up: ## Start full stack (Gaia + ChromaDB)
	$(COMPOSE_CMD) -f docker-compose.yml up -d

compose-up-ollama: ## Start full stack + Ollama
	LLM_ENDPOINT=http://llama:11434 $(COMPOSE_CMD) -f docker-compose.yml up -d

compose-down: ## Stop and remove all compose services
	$(COMPOSE_CMD) -f docker-compose.yml down

compose-logs: ## Tail compose service logs
	$(COMPOSE_CMD) -f docker-compose.yml logs -f

compose-build: ## Rebuild all compose images
	$(COMPOSE_CMD) -f docker-compose.yml build --pull
