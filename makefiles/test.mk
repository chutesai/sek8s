PHONY: test
test: ##@local Run test suite
test:
	${DC} run --rm --no-deps test