# Developer convenience targets. Run from the backend/ directory.
.PHONY: install lint type test cov mutation check demo demo-release clean

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

# End-to-end release demo (J4). Starts its own API on a free port and uses any
# live provider it can reach, falling back to the offline mock.
demo:
	python scripts/demo.py --compare

# Release sign-off: the same demo, but it fails unless the debate really spanned
# two providers (e.g. a local Ollama plus Anthropic with ANTHROPIC_API_KEY set).
demo-release:
	python scripts/demo.py --compare --strict-providers

clean:
	rm -rf .mutmut-cache .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage demo-output
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
