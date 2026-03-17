.PHONY: install lint test coverage clean help

PYTHON   ?= python3
TARGET   ?=

help:
	@echo "todo-sync plugin development targets:"
	@echo "  make install TARGET=/path/to/repo    Install plugin into a target repo"
	@echo "  make lint                             Lint Python code"
	@echo "  make test                             Run unit tests"
	@echo "  make coverage                         Run tests with coverage report"
	@echo "  make clean                            Remove __pycache__ and .pyc files"

install:
	@if [ -z "$(TARGET)" ]; then \
	  echo "Error: TARGET is required. Example: make install TARGET=/path/to/repo"; \
	  exit 1; \
	fi
	@bash install.sh

lint:
	@echo "Running flake8..."
	@$(PYTHON) -m flake8 scripts/sync.py tests/ --max-line-length=100 || true
	@echo "Running mypy..."
	@$(PYTHON) -m mypy scripts/sync.py --ignore-missing-imports || true

test:
	@echo "Running tests..."
	@$(PYTHON) -m pytest tests/ -v

coverage:
	@echo "Running tests with coverage..."
	@$(PYTHON) -m pytest tests/ --cov=scripts --cov-report=term-missing

clean:
	@find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned up"
