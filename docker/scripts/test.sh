#!/bin/bash
poetry run pytest -s --tb=native --durations=5 --cov=sek8s --cov=sek8s_common --cov-report=html tests
poetry run coverage report --fail-under=0
