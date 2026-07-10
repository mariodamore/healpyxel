# Makefile for healpyxel — pure Python + Quarto workflow (ADR-007)

.DEFAULT_GOAL := help

PYTHON := python3
PIP := pip

###############################################################
# Installation targets
###############################################################

install: ## Install package in normal mode
	$(PIP) install .

install-dev: ## Install package in editable mode with dev dependencies
	$(PIP) install -e ".[dev]"

###############################################################
# Testing
###############################################################

test: ## Run pytest suite
	$(PYTHON) -m pytest tests/ -v

test-quick: ## Run pytest (short output)
	$(PYTHON) -m pytest tests/ -q

test-cov: ## Run pytest with coverage
	$(PYTHON) -m pytest tests/ --cov=healpyxel --cov-report=term-missing

###############################################################
# Documentation
###############################################################

quartodoc-build: ## build the quartodoc API reference pages into reference/
	python -m quartodoc build

docs: ## Build Quarto documentation
	quarto render

docs-preview: ## Preview Quarto documentation locally
	quarto preview

docs-clean: ## Remove rendered documentation
	rm -rf docs/_site/ docs/.quarto/

###############################################################
# Notebook conversion
###############################################################

notebooks-export: ## Sync .py:percent notebooks (jupytext --sync)
	jupytext --sync notebooks/*.py

notebooks-to-ipynb: ## Convert .py:percent to .ipynb for viewing
	for f in notebooks/*.py; do jupytext --to ipynb --output $$f.ipynb $$f; done

###############################################################
# Cleaning targets
###############################################################

clean-pyc: ## Remove Python cache files
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -rf {} +
	find . -name '*~' -exec rm -f {} +

clean-build: ## Remove build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .eggs/

clean-docs: ## Remove rendered documentation
	rm -rf docs/_site/
	rm -rf docs/.quarto/

clean-test: ## Remove test artifacts
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/

clean: clean-pyc clean-build clean-docs clean-test ## Clean all generated files

check: ## Check package setup and dependencies
	@echo "=== Python Version ==="
	@$(PYTHON) --version
	@echo "\n=== Package Info ==="
	@$(PIP) show healpyxel || echo "Package not installed (run: make install-dev)"
	@$(PYTHON) -c "import healpyxel; print('  ✓ Package imports OK')" 2>/dev/null || echo "  ✗ Import failed"
	@$(PYTHON) -c "import healpyxel; print(f'  ℹ Version: {healpyxel.__version__}')"
	@echo "\n=== Dependencies ==="
	@$(PIP) list | grep -E "pandas|numpy|pyarrow|shapely|healpy|pytest|jupytext|quarto|quartodoc"

audit: ## Run complete package audit
	@echo "\n=== HEALPYXEL PACKAGE AUDIT ===\n"
	@echo "=== Package Structure ==="
	@test -f pyproject.toml && echo "  ✓ pyproject.toml" || echo "  ✗ pyproject.toml"
	@test -f LICENSE && echo "  ✓ LICENSE" || echo "  ✗ LICENSE"
	@test -f .gitignore && echo "  ✓ .gitignore" || echo "  ✗ .gitignore"
	@test -f CHANGELOG.md && echo "  ✓ CHANGELOG.md" || echo "  ✗ CHANGELOG.md"
	@test -f README.md && echo "  ✓ README.md" || echo "  ✗ README.md"
	@echo "=== Python Package ==="
	@test -f healpyxel/__init__.py && echo "  ✓ healpyxel/__init__.py" || echo "  ✗ healpyxel/__init__.py"
	@ls healpyxel/*.py 2>/dev/null | wc -l | xargs -I {} echo "  ℹ {} Python modules"
	@test -f healpyxel/_modidx.py && echo "  ✗ _modidx.py still exists (nbdev artifact)" || echo "  ✓ _modidx.py removed"
	@test -d healpyxel/tests && echo "  ✗ healpyxel/tests/ still exists (should be deleted)" || echo "  ✓ healpyxel/tests/ removed"
	@$(PYTHON) -c "import healpyxel; print('  ✓ Package imports OK')" 2>/dev/null || echo "  ✗ Import failed"
	@echo "=== Tests ==="
	@test -d tests && echo "  ✓ tests/" || echo "  ✗ tests/"
	@test -f tests/conftest.py && echo "  ✓ tests/conftest.py" || echo "  ✗ tests/conftest.py"
	@ls tests/test_*.py 2>/dev/null | wc -l | xargs -I {} echo "  ℹ {} test files"
	@echo "=== Documentation ==="
	@test -d docs && echo "  ✓ docs/" || echo "  ✗ docs/"
	@test -d notebooks && echo "  ✓ notebooks/" || echo "  ✗ notebooks/"
	@ls notebooks/*.py 2>/dev/null | wc -l | xargs -I {} echo "  ℹ {} .py:percent notebooks"
	@echo "=== Notebooks (nbs/) ==="
	@test -d nbs && echo "  ✗ nbs/ still exists (should be deleted)" || echo "  ✓ nbs/ removed"

###############################################################
# Utility
###############################################################

tree: ## Show project structure
	tree -L 2 -I '__pycache__|*.pyc|.git|.ipynb_checkpoints|*.egg-info|_site|.quarto'

help: ## Show this help message
	@echo "\nHealpyxel Development Commands (pure Python + Quarto)\n"
	@awk -F':[[:space:]]*.*## ' '/^[a-zA-Z0-9_.-]+ *:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""

.PHONY: install install-dev test test-quick test-cov docs docs-preview docs-clean \
        notebooks-export notebooks-to-ipynb \
        clean-pyc clean-build clean-docs clean-test clean check audit tree help
