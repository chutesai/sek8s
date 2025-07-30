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
build: args ?= -f docker/Dockerfile --build-arg PROJECT_DIR=. --network=host --build-arg BUILDKIT_INLINE_CACHE=1
build:
	DOCKER_BUILDKIT=1 docker build --progress=plain --target production -t ${prod_image} ${args} .
	DOCKER_BUILDKIT=1 docker build --progress=plain --target development -t ${dev_image} --cache-from ${prod_image} ${args} .

.PHONY: infrastructure
infrastructure: ##@development Set up infrastructure for tests
infrastructure:
	k3d cluster create dev --config config/k3d-config.yml

.PHONY: clean
clean: ##@development Clean up any dependencies
clean:
	k3d cluster delete dev

.PHONY: redeploy
redeploy: ##@development Redeploy infrastructure
redeploy: clean infrastructure

.PHONY: config
ci: ##@development Run CI pipeline
ci: clean build infrastructure lint test clean