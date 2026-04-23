CHANGELOG_SCOPES := $(shell ls changelogs)
CHANGELOG_SCOPE := $(filter $(CHANGELOG_SCOPES),$(MAKECMDGOALS))

# Scopes that aren't already registered as package no-op targets
CHANGELOG_ONLY_SCOPES := $(filter-out $(PACKAGES),$(CHANGELOG_SCOPE))
ifneq ($(CHANGELOG_ONLY_SCOPES),)
$(CHANGELOG_ONLY_SCOPES):
	@:
endif

define CHANGELOG_TEMPLATE
### Added
-

### Changed
-

### Fixed
-

### Removed
-
endef
export CHANGELOG_TEMPLATE

.PHONY: changelog
changelog: ##@changelog Create a changelog fragment for the current branch
ifeq ($(CHANGELOG_SCOPE),)
	$(error Usage: make changelog <scope>  — valid scopes: $(CHANGELOG_SCOPES))
endif
	@mkdir -p changelogs/$(CHANGELOG_SCOPE)/unreleased
	@fragment="changelogs/$(CHANGELOG_SCOPE)/unreleased/$(BRANCH_NAME).md"; \
	if [ -f "$$fragment" ]; then \
		echo "$$fragment already exists"; \
		exit 1; \
	else \
		echo "$$CHANGELOG_TEMPLATE" > "$$fragment"; \
		echo "Created $$fragment"; \
	fi

.PHONY: promote-changelogs
promote-changelogs: ##@changelog Promote changelog fragments into CHANGELOG.md
	python scripts/promote_changelogs.py --promote

.PHONY: check-changelogs
check-changelogs: ##@changelog Verify no orphaned changelog fragments remain
	python scripts/promote_changelogs.py --check --strict
