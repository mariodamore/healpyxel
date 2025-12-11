# Healpyxel Package Audit & TODO

## ✅ What's Working

### Package Structure
- ✅ `settings.ini` - nbdev configuration with version 0.1.0
- ✅ `pyproject.toml` - Modern Python packaging
- ✅ `healpyxel/__init__.py` - Package initializer with `__version__`
- ✅ `Makefile` - Development workflow automation
- ✅ `README.md` - Generated from index.ipynb
- ✅ `test_data/` - 59MB test dataset (15 files)
- ✅ Git repository initialized with 2 commits

### Documentation
- ✅ `nbs/index.ipynb` - Package homepage
- ✅ `nbs/00_setup.ipynb` - Setup guide
- ✅ `nbs/90_example_streaming.ipynb` - Streaming example
- ✅ `docs/` - Generated documentation (HTML)
- ✅ `QUICKSTART.md` - Quick start guide

### Original Scripts (Ready for Migration)
- ✅ `healpix_sidecar.py` (24KB)
- ✅ `healpix_aggregate.py` (41KB)
- ✅ `healpix_accumulator.py` (22KB)
- ✅ `healpix_finalize.py` (16KB)

---

## ⚠️ Issues Found

### 1. **Critical: No Python Modules Yet**
**Problem:** Package has no actual functionality - only `__init__.py` with version
```bash
$ python -c "import healpyxel; print(dir(healpyxel))"
['__version__', '__doc__', '__file__', ...]  # No actual functions!
```

**Fix:** Create nbdev notebooks to export Python modules:
- `nbs/00_core.ipynb` → `healpyxel/core.py`
- `nbs/01_sidecar.ipynb` → `healpyxel/sidecar.py`
- `nbs/02_aggregate.ipynb` → `healpyxel/aggregate.py`
- `nbs/03_accumulator.ipynb` → `healpyxel/accumulator.py`
- `nbs/04_finalize.ipynb` → `healpyxel/finalize.py`

### 2. **Metadata Inconsistencies**

**settings.ini:**
```ini
copyright = 2025 onwards, Your Name  # ← Should be "Mario D'Amore"
user = mariodamore                    # ✓ Correct
```

**pyproject.toml:**
```toml
authors = [
    {name = "Your Name", email = "your.email@example.com"}  # ← Wrong!
]
```
Should match settings.ini (Mario D'Amore, mario.damore@dlr.de)

### 3. **Missing Essential Files**

#### A. **LICENSE File**
- Settings declares `license = apache2`
- But no `LICENSE` file exists in root
- **Impact:** Can't legally distribute package

**Fix:**
```bash
# Create LICENSE file with Apache 2.0 text
curl -sL https://www.apache.org/licenses/LICENSE-2.0.txt > LICENSE
```

#### B. **.gitignore**
- No `.gitignore` exists
- **Impact:** May accidentally commit build artifacts, cache files, etc.

**Should ignore:**
```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
dist/
build/

# nbdev
_docs/
_proc/
.quarto/

# Jupyter
.ipynb_checkpoints/
*.ipynb~

# IDEs
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

#### C. **CHANGELOG.md**
- No changelog for version tracking
- **Impact:** Users can't see what changed between versions

#### D. **CONTRIBUTING.md**
- No contribution guidelines
- **Impact:** Others won't know how to contribute

### 4. **Missing Tests**

**Current state:**
```bash
$ ls tests/
# Empty directory!
```

**What's needed:**
- Unit tests for all modules
- Integration tests for workflows
- Test fixtures using test_data/

**Example structure:**
```
tests/
├── __init__.py
├── conftest.py              # pytest fixtures
├── test_core.py
├── test_sidecar.py
├── test_accumulator.py
└── test_finalize.py
```

### 5. **CLI Entry Points Not Defined**

**pyproject.toml has:**
```toml
[project.scripts]
healpyxel = "healpyxel.cli:main"  # ← But healpyxel/cli.py doesn't exist!
```

**Current workaround:** Users must run Python scripts directly
```bash
python healpix_sidecar.py --input data.parquet  # Works
healpyxel sidecar --input data.parquet          # Doesn't work!
```

**Fix:** Create `nbs/05_cli.ipynb` → `healpyxel/cli.py` with proper entry points

### 6. **Documentation Gaps**

#### Missing Documentation:
- ❌ API Reference (auto-generated from docstrings)
- ❌ User Guide / Tutorial
- ❌ Installation instructions for different platforms
- ❌ Troubleshooting section
- ❌ Citation information (for academic use)
- ❌ Performance benchmarks

#### Existing Documentation Issues:
- `README.md` is minimal (only from index.ipynb)
- No architecture diagrams in docs
- No comparison with other tools
- Missing example outputs/visualizations

### 7. **Dependencies Issues**

**Incomplete optional dependencies:**
```toml
[project.optional-dependencies]
dev = ["nbdev>=2.3.12", "jupyter"]
tdigest = ["tdigest"]
duckdb = ["duckdb"]
```

**Missing:**
- Testing tools: `pytest`, `pytest-cov`
- Documentation: `quarto`, `quartodoc`
- Code quality: `black`, `ruff`, `mypy`
- Build tools: `build`, `twine`

### 8. **GitHub Integration Missing**

#### A. **GitHub Actions CI/CD**
No `.github/workflows/` directory

**Should have:**
- `test.yml` - Run tests on push/PR
- `docs.yml` - Deploy docs to GitHub Pages
- `release.yml` - Publish to PyPI on release

#### B. **GitHub Metadata**
No `.github/` directory with:
- `ISSUE_TEMPLATE/` - Bug reports, feature requests
- `PULL_REQUEST_TEMPLATE.md`
- `CODEOWNERS` - Define maintainers

### 9. **Version Management**

**Current:** Hardcoded in 2 places
- `settings.ini`: `version = 0.1.0`
- `pyproject.toml`: `version = "0.1.0"`

**Problem:** Easy to get out of sync

**Better approach:** Single source of truth
```python
# healpyxel/__init__.py
__version__ = "0.1.0"  # ← Read from here

# pyproject.toml
[project]
dynamic = ["version"]  # Get from package

[tool.setuptools.dynamic]
version = {attr = "healpyxel.__version__"}
```

### 10. **Package Validation**

**Missing validation checks:**
```bash
# Check package can be built
python -m build

# Check package metadata
twine check dist/*

# Validate imports work
python -c "import healpyxel; print(healpyxel.__version__)"

# Check CLI works
healpyxel --help
```

---

## 📋 Priority Action Plan

### Phase 1: Critical (Do First) 🔴

1. **Fix metadata inconsistencies**
   - [ ] Update `settings.ini` copyright
   - [ ] Update `pyproject.toml` author info
   - [ ] Ensure all metadata matches

2. **Add LICENSE file**
   - [ ] Copy Apache 2.0 license text
   - [ ] Add copyright notice with Mario D'Amore

3. **Add .gitignore**
   - [ ] Create comprehensive .gitignore
   - [ ] Clean up any accidentally committed files

4. **Convert core scripts to notebooks**
   - [ ] Create `00_core.ipynb` (utilities)
   - [ ] Create `01_sidecar.ipynb`
   - [ ] Create `02_aggregate.ipynb`
   - [ ] Create `03_accumulator.ipynb`
   - [ ] Create `04_finalize.ipynb`
   - [ ] Run `make export` to generate modules

### Phase 2: Essential (Do Soon) 🟡

5. **Add basic tests**
   - [ ] Create `tests/conftest.py` with fixtures
   - [ ] Add unit tests for each module
   - [ ] Use test_data/ for integration tests
   - [ ] Run `make test` to validate

6. **Setup CLI properly**
   - [ ] Create `05_cli.ipynb`
   - [ ] Define all CLI commands
   - [ ] Test: `healpyxel --help`

7. **Fix dependencies**
   - [ ] Add dev dependencies (pytest, black, etc.)
   - [ ] Add all optional dependencies
   - [ ] Test: `make install-dev`

8. **Add CHANGELOG.md**
   - [ ] Start with 0.1.0 initial release
   - [ ] Use keepachangelog.com format

### Phase 3: Important (Do This Week) 🟢

9. **GitHub Actions CI/CD**
   - [ ] Add `.github/workflows/test.yml`
   - [ ] Add `.github/workflows/docs.yml`
   - [ ] Test workflows on GitHub

10. **Improve documentation**
    - [ ] Add comprehensive README
    - [ ] Create tutorials notebook
    - [ ] Add API reference
    - [ ] Include example outputs

11. **Package validation**
    - [ ] Test build: `python -m build`
    - [ ] Test install: `pip install dist/*.whl`
    - [ ] Test import: `python -c "import healpyxel"`
    - [ ] Test CLI: `healpyxel --help`

### Phase 4: Nice to Have (Later) ⚪

12. **Add CONTRIBUTING.md**
13. **Add GitHub issue templates**
14. **Add code quality tools (mypy, ruff)**
15. **Setup GitHub Pages for docs**
16. **Add performance benchmarks**
17. **Create comparison with other tools**
18. **Add citation information (CITATION.cff)**

---

## 🔧 Quick Fix Commands

```bash
cd /path/to/healpyxel

# 1. Fix metadata in settings.ini
sed -i 's/Your Name/Mario D'"'"'Amore/g' settings.ini

# 2. Add LICENSE
curl -sL https://www.apache.org/licenses/LICENSE-2.0.txt > LICENSE

# 3. Add .gitignore
cat > .gitignore << 'EOF'
__pycache__/
*.py[cod]
.Python
*.egg-info/
dist/
build/
_docs/
_proc/
.ipynb_checkpoints/
.DS_Store
EOF

# 4. Fix pyproject.toml author
# (Manual edit needed)

# 5. Create empty test structure
mkdir -p tests
touch tests/__init__.py tests/conftest.py

# 6. Export notebooks (once created)
make export

# 7. Run tests
make test

# 8. Build docs
make docs
```

---

## 🎯 Current Status Summary

| Category | Status | Notes |
|----------|--------|-------|
| Package Structure | ✅ 80% | Basics done, needs modules |
| Metadata | ⚠️ 60% | Inconsistencies need fixing |
| Code | ❌ 0% | No modules exported yet |
| Tests | ❌ 0% | Empty tests/ directory |
| Documentation | ⚠️ 40% | Basic structure, needs content |
| CLI | ❌ 0% | Defined but not implemented |
| CI/CD | ❌ 0% | No GitHub Actions |
| License | ❌ 0% | No LICENSE file |

**Overall: 28% Complete** - Good structure, but needs core functionality!

---

## 📚 References

- [nbdev Documentation](https://nbdev.fast.ai/)
- [Python Packaging Guide](https://packaging.python.org/)
- [Keep a Changelog](https://keepachangelog.com/)
- [Semantic Versioning](https://semver.org/)
- [Apache 2.0 License](https://www.apache.org/licenses/LICENSE-2.0)
