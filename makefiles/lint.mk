.PHONY: bandit
bandit: ##@lint Run bandit
bandit:
	${DC} run --rm --no-deps bandit -r $(SRC_DIRS)

.PHONY: black
black: ##@lint Run black
black:
	${DC} run --rm --no-deps black $(LINT_DIRS)

.PHONY: flake8
flake8: ##@lint Run flake8
flake8:
	${DC} run --rm --no-deps flake8 --config .flake8 $(LINT_DIRS)

.PHONY: isort
isort: ##@lint Run isort
isort:
	${DC} run --rm --no-deps isort --diff --check-only --quiet $(LINT_DIRS)

.PHONY: mypy
mypy: ##@lint Run mypy
mypy:
	${DC} run --rm --no-deps mypy $(MYPY_ARGS)

.PHONY: lint
lint: ##@lint Run lint tools
lint: bandit black flake8 isort mypy
