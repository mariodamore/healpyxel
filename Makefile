# Makefile for healpyxel nbdev package
# Remember to activate the appropriate conda environment first!
.DEFAULT_GOAL := help

###############################################################
# Installation targets
###############################################################

install: ## Install package in normal mode
	pip install .

install-dev: ## Install package in editable/development mode with all dependencies
	pip install -e ".[dev]"

install-hooks: install-dev ## Install package in dev mode and setup nbdev git hooks
	nbdev_install_hooks

###############################################################
# nbdev workflow targets
###############################################################

export: ## Export notebooks to Python modules
	nbdev_export

test: ## Run tests from notebooks
	nbdev_test

docs: ## Build documentation from notebooks
	nbdev_docs

preview: ## Preview documentation locally
	nbdev_preview

prepare: ## Run nbdev_export, nbdev_test, and nbdev_clean (full prepare workflow)
	nbdev_prepare

readme: ## Generate README.md from index.ipynb
	nbdev_readme

clean-nbs: ## Clean notebooks (remove unnecessary metadata)
	nbdev_clean

###############################################################
# Development workflow shortcuts
###############################################################

dev: export test ## Quick development cycle: export and test

build: prepare docs ## Full build: prepare everything and generate docs

watch: ## Watch notebooks and auto-export on changes (requires watchdog)
	@echo "Watching notebooks for changes (Ctrl+C to stop)..."
	@while true; do \
		inotifywait -q -e modify nbs/*.ipynb 2>/dev/null && nbdev_export; \
	done || echo "Install inotify-tools for auto-watch: sudo apt install inotify-tools"

###############################################################
# Cleaning targets
###############################################################

clean-pyc: ## Remove Python cache files
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -rf {} +
	find . -name '*~' -exec rm -f {} +

clean-build: ## Remove build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .eggs/

clean-docs: ## Remove generated documentation
	rm -rf docs/_site/
	rm -rf docs/.quarto/

clean-test: ## Remove test artifacts
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/

clean: clean-pyc clean-build clean-docs clean-test ## Clean all generated files

###############################################################
# Git shortcuts
###############################################################

status: ## Show git status and dirty notebooks
	@echo "=== Git Status ==="
	git status -s
	@echo "\n=== Checking for dirty notebooks ==="
	nbdev_clean --clear_all --fname=nbs/*.ipynb || echo "Some notebooks may have unsaved changes"

commit: prepare ## Prepare and show status before commit
	@echo "Package prepared. Review changes and commit manually with:"
	@echo "  git add ."
	@echo "  git commit -m 'your message'"

###############################################################
# Package distribution
###############################################################

dist: clean prepare ## Build source and wheel distributions
	python -m build

upload-test: dist ## Upload to TestPyPI
	twine upload --repository testpypi dist/*

upload: dist ## Upload to PyPI (use with caution!)
	twine upload --repository healpyxel dist/*

###############################################################
# Utility targets
###############################################################

jupyter: ## Start Jupyter Lab in nbs directory
	jupyter lab nbs/

notebook: ## Start Jupyter Notebook in nbs directory
	jupyter notebook nbs/

check: ## Check package setup and dependencies
	@echo "=== Python Version ==="
	@python --version
	@echo "\n=== Conda Environment ==="
	@conda info | grep "active environment" || echo "Not using conda"
	@echo "\n=== Installed Packages ==="
	@pip list | grep -E "nbdev|jupyter|pandas|healpy|pytest"
	@echo "\n=== Package Info ==="
	@pip show healpyxel || echo "Package not installed (run: make install-dev)"

audit: ## Run complete package audit
	@echo "\n\033[1m=== HEALPYXEL PACKAGE AUDIT ===\033[0m\n"
	@echo "\033[36m📦 Package Structure:\033[0m"
	@test -f settings.ini && echo "  ✓ settings.ini" || echo "  ✗ settings.ini"
	@test -f pyproject.toml && echo "  ✓ pyproject.toml" || echo "  ✗ pyproject.toml"
	@test -f LICENSE && echo "  ✓ LICENSE" || echo "  ✗ LICENSE"
	@test -f .gitignore && echo "  ✓ .gitignore" || echo "  ✗ .gitignore"
	@test -f CHANGELOG.md && echo "  ✓ CHANGELOG.md" || echo "  ✗ CHANGELOG.md"
	@test -f README.md && echo "  ✓ README.md" || echo "  ✗ README.md"
	@echo "\n\033[36m📚 Documentation:\033[0m"
	@test -d docs && echo "  ✓ docs/" || echo "  ✗ docs/"
	@test -f nbs/index.ipynb && echo "  ✓ nbs/index.ipynb" || echo "  ✗ nbs/index.ipynb"
	@test -f nbs/00_setup.ipynb && echo "  ✓ nbs/00_setup.ipynb" || echo "  ✗ nbs/00_setup.ipynb"
	@echo "\n\033[36m🐍 Python Package:\033[0m"
	@test -f healpyxel/__init__.py && echo "  ✓ healpyxel/__init__.py" || echo "  ✗ healpyxel/__init__.py"
	@test -d healpyxel && ls healpyxel/*.py 2>/dev/null | wc -l | xargs -I {} echo "  ℹ {} Python modules"
	@python -c "import healpyxel; print('  ✓ Package imports successfully')" 2>/dev/null || echo "  ✗ Package import failed"
	@python -c "import healpyxel; print(f'  ℹ Version: {healpyxel.__version__}')" 2>/dev/null || echo "  ✗ No __version__"
	@echo "\n\033[36m🧪 Tests:\033[0m"
	@test -d tests && echo "  ✓ tests/" || echo "  ✗ tests/"
	@test -f tests/conftest.py && echo "  ✓ tests/conftest.py" || echo "  ✗ tests/conftest.py"
	@ls tests/test_*.py 2>/dev/null | wc -l | xargs -I {} echo "  ℹ {} test files"
	@echo "\n\033[36m📊 Test Data:\033[0m"
	@test -d test_data && echo "  ✓ test_data/" || echo "  ✗ test_data/"
	@test -d test_data/batches && ls test_data/batches/*.parquet 2>/dev/null | wc -l | xargs -I {} echo "  ℹ {} batch files" || echo "  ✗ No batches"
	@echo "\n\033[36m🔧 Git Status:\033[0m"
	@git status &>/dev/null && echo "  ✓ Git repository" || echo "  ✗ Not a git repository"
	@git log --oneline -1 2>/dev/null | xargs -I {} echo "  ℹ Last commit: {}" || true
	@echo "\n\033[33m📋 See PACKAGE_AUDIT.md for detailed issues and fixes\033[0m\n"

tree: ## Show project structure
	tree -L 2 -I '__pycache__|*.pyc|.git|.ipynb_checkpoints|*.egg-info'

###############################################################
# Self Documenting Commands
###############################################################

help: ## Show this help message
	@echo "\n\033[1mHealpyxel Development Commands\033[0m\n"
	@awk -F':[[:space:]]*.*## ' '/^[a-zA-Z0-9_.-]+ *:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""

.PHONY: install install-dev install-hooks export test docs preview prepare readme clean-nbs \
        dev build watch clean-pyc clean-build clean-docs clean-test clean status commit \
        dist upload-test upload jupyter notebook check tree help
