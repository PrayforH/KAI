.PHONY: install test lint typecheck agent-check agent-pack agent-determinism readiness release-infra verify dev-up dev-up-cc-switch dev-down migrate e2e web-test web-build docker-config docker-build docker-up docker-up-observability docker-down docker-e2e smoke-daytona

DOCKER_COMPOSE = docker compose --env-file deploy/docker-compose/.env.docker -f deploy/docker-compose/compose.yaml

install:
	uv sync --group dev

test:
	uv run python -m scripts.run_local_tests

lint:
	uv run ruff check src tests

typecheck:
	# Baseline gate: strict-mode debt exists (284 errors at v0.1.0, tracked in
	# docs/runbooks/release-0.1.0-checklist.md). Fail only when the count grows.
	@set -e; \
	errors="$$(uv run pyright 2>&1 | uv run python -c 'import re,sys; m=re.search(r"([0-9]+) errors", sys.stdin.read()); print(m.group(1) if m else "0")')"; \
	echo "pyright errors: $${errors} (baseline 284)"; \
	test "$${errors}" -le 284

agent-check:
	uv run python scripts/check_agent_packages.py

agent-pack:
	uv run python scripts/check_agent_packages.py --output dist/agents

agent-determinism:
	uv run python scripts/verify_agent_determinism.py

readiness:
	uv run python scripts/final_readiness.py

release-infra:
	uv run python scripts/check_release_infrastructure.py

verify: lint typecheck agent-check agent-determinism readiness test

dev-up:
	bash scripts/dev_up.sh

dev-up-cc-switch:
	HARNESS_RUNTIME=claude-sdk bash scripts/dev_up.sh

dev-down:
	bash scripts/dev_down.sh

migrate:
	uv run alembic upgrade head

e2e:
	uv run python scripts/wait_for_local_services.py
	uv run python scripts/e2e_fake_runtime.py

web-test:
	cd web/harness-console && npm test

web-build:
	cd web/harness-console && npm run build

docker-config:
	$(DOCKER_COMPOSE) config --quiet

docker-build:
	$(DOCKER_COMPOSE) build

docker-up:
	$(DOCKER_COMPOSE) up -d --wait

docker-up-observability:
	$(DOCKER_COMPOSE) --profile observability up -d --wait

docker-down:
	$(DOCKER_COMPOSE) down

docker-e2e:
	uv run python scripts/e2e_docker.py

smoke-daytona:
	@set -a; \
	if [ -f deploy/docker-compose/.env.docker ]; then . deploy/docker-compose/.env.docker; fi; \
	set +a; \
	uv run python scripts/smoke_daytona.py
