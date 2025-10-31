.PHONY: install
install: ##@development Instal development dependencies
install: venv
	mkdir bin
	curl -L -o bin/opa https://openpolicyagent.org/downloads/v1.3.0/opa_linux_amd64_static
	chmod 755 ./bin/opa

.PHONY: venv
venv: ##@development Set up virtual environment
venv:
	${POETRY} install

.PHONY: build
buid: ##@development Build the docker images
build: prod_image ?= ${PROJECT}:${BRANCH_NAME}-${BUILD_NUMBER}
build: dev_image ?= ${PROJECT}_development:${BRANCH_NAME}-${BUILD_NUMBER}
build: args ?= -f docker/Dockerfile --build-arg PROJECT_DIR=${PROJECT} --network=host --build-arg BUILDKIT_INLINE_CACHE=1
build:
	@echo "Building images for: $$PROJECT"; \
	pkg_name="$$PROJECT"; \
	pkg_version=$$(if [ -f "$$pkg_name/VERSION" ]; then head $$pkg_name/VERSION; else echo "dev"; fi); \
	if [ -f "docker/Dockerfile" ]; then \
		echo "Building images for $$pkg_name (version: $$pkg_version)"; \
		dockerfile="docker/Dockerfile"; \
		available_targets=$$(docker build --progress=plain -f $$dockerfile --target help . 2>/dev/null | grep "^FROM" | sed 's/.*AS \([^[:space:]]*\).*/\1/' || echo ""); \
		if [ -z "$$available_targets" ]; then \
			available_targets=$$(grep -i "^FROM.*AS" $$dockerfile | sed 's/.*AS[[:space:]]*\([^[:space:]]*\).*/\1/' | tr '[:upper:]' '[:lower:]' || echo "production development"); \
		fi; \
		for stage_target in $$available_targets; do \
			if [[ "$$stage_target" == production* ]]; then \
				if [[ "$$stage_target" == *-* ]]; then \
					gpu_suffix=$$(echo $$stage_target | sed 's/production-//'); \
					image_tag="$$pkg_version-$$gpu_suffix"; \
					image_name="$$pkg_name"; \
				else \
					image_tag="$$pkg_version"; \
					image_name="$$pkg_name"; \
				fi; \
				echo "Building production target: $$stage_target -> $$image_name:$$image_tag"; \
				DOCKER_BUILDKIT=1 docker build --progress=plain --target $$stage_target \
					-f $$dockerfile \
					-t $$image_name:${BRANCH_NAME}-${BUILD_NUMBER} \
					-t $$image_name:$$image_tag \
					--build-arg PROJECT_DIR=$$pkg_name \
					--build-arg PROJECT=$$pkg_name \
					${args} .; \
			elif [[ "$$stage_target" == development* ]]; then \
				if [[ "$$stage_target" == *-* ]]; then \
					gpu_suffix=$$(echo $$stage_target | sed 's/development-//'); \
					image_tag="$$pkg_version-$$gpu_suffix"; \
					image_name="$${pkg_name}_dev"; \
				else \
					image_tag="$$pkg_version"; \
					image_name="$${pkg_name}_dev"; \
				fi; \
				echo "Building development target: $$stage_target -> $$image_name:$$image_tag"; \
				DOCKER_BUILDKIT=1 docker build --progress=plain --target $$stage_target \
					-f $$dockerfile \
					-t $$image_name:${BRANCH_NAME}-${BUILD_NUMBER} \
					-t $$image_name:$$image_tag \
					--build-arg PROJECT_DIR=$$pkg_name \
					--build-arg PROJECT=$$pkg_name \
					--cache-from $$pkg_name:${BRANCH_NAME}-${BUILD_NUMBER} \
					${args} .; \
			fi; \
		done; \
	else \
		echo "Skipping $$pkg_name: docker/$$pkg_name/Dockerfile not found"; \
	fi;

.PHONY: infrastructure
infrastructure: ##@development Set up infrastructure for tests
infrastructure:
	${DC} up opa registry -d
	./tests/scripts/setup-test-images.sh

.PHONY: clean
clean: ##@development Clean up any dependencies
clean:
	${DC} down opa registry --remove-orphans --volumes
	docker network prune -f
	docker container prune -f

.PHONY: k3s-infrastructure
k3s-infrastructure: ##@development Set up infrastructure for tests
k3s-infrastructure:
	k3d cluster create dev --config docker/config/k3d-config.yml

.PHONY: k3s-clean
k3s-clean: ##@development Clean up any dependencies
k3s-clean:
	k3d cluster delete dev

.PHONY: redeploy
redeploy: ##@development Redeploy infrastructure
redeploy: k3s-clean k3s-infrastructure

.PHONY: ci
ci: ##@development Run CI pipeline
ci: clean build infrastructure lint test clean