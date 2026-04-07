.PHONY: bandit-local
bandit-local: ##@lint Run bandit
bandit-local:
	${POETRY} run bandit -r $(SRC_DIRS)

.PHONY: black-local
black-local: ##@lint Run black
black-local:
	${POETRY} run black $(LINT_DIRS)

.PHONY: flake8-local
flake8-local: ##@lint Run flake8
flake8-local:
	${POETRY} run flake8 --config .flake8 $(LINT_DIRS)

.PHONY: isort-local
isort-local: ##@lint Run isort
isort-local:
	${POETRY} run isort --diff --check-only --quiet $(LINT_DIRS)

.PHONY: mypy-local
mypy-local: ##@lint Run mypy
mypy-local:
	${POETRY} run mypy $(MYPY_ARGS)

.PHONY: lint-local
lint-local: ##@lint Run lint tools
lint-local: bandit-local black-local flake8-local isort-local mypy-local

.PHONY: clean-imports
clean-imports: ##@local Remove unused imports
clean-imports:
	autoflake --in-place --remove-all-unused-imports --recursive $(SRC_DIRS) tests

.PHONY: reformat
reformat: ##@local Reformat module
reformat: clean-imports
	${POETRY} run isort --overwrite-in-place $(LINT_DIRS)
	${POETRY} run black $(LINT_DIRS)

.PHONY: generate-openapi
generate-openapi: ##@local Generate system manager OpenAPI spec to docs/system_manager_openapi.json
	${POETRY} run python scripts/generate_openapi.py

PHONY: test-local
test-local: ##@local Run test suite
test-local: venv
	${POETRY} run pytest -s --tb=native --durations=5 $(COV_ARGS) --cov-report=html tests
	${POETRY} run coverage report --fail-under=50

.PHONY: promote-changelogs
promote-changelogs: ##@local Promote changelog fragments into CHANGELOG.md
	python scripts/promote_changelogs.py --promote

.PHONY: check-changelogs
check-changelogs: ##@local Verify no orphaned changelog fragments remain
	python scripts/promote_changelogs.py --check --strict
