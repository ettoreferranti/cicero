# Developer convenience targets. Run from the backend/ directory.
.PHONY: install lint type test cov mutation check clean

install:
	uv pip install -e ".[dev]"

lint:
	ruff check .

type:
	mypy cicero

test:
	pytest

cov:
	pytest --cov=cicero --cov-report=term-missing

mutation:
	mutmut run || true
	python scripts/check_mutation_score.py --min 80

# The full local quality gate, mirroring CI.
check: lint type cov mutation

clean:
	rm -rf .mutmut-cache .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
