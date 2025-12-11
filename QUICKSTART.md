# Healpyxel Package - Quick Start

## ✅ Package Setup Complete!

The `healpyxel` package structure has been created with nbdev.

### 📁 Package Structure

```
healpyxel/
├── nbs/                          # Notebooks (source of truth)
│   ├── index.ipynb              # Package homepage ✓
│   ├── 00_setup.ipynb           # Setup guide ✓
│   └── 90_example_streaming.ipynb  # Streaming example ✓
│
├── healpyxel/                    # Python package (auto-generated)
├── tests/                        # Tests (auto-generated)
├── docs/                         # Documentation (auto-generated)
│
├── test_data/                    # Test datasets ✓
│   ├── batches/                 # 10 sequential batches
│   ├── samples/                 # 3 size-based samples
│   └── validation/              # 2 validation files
│
├── healpix_*.py                  # Original scripts (to be migrated)
├── settings.ini                  # nbdev configuration ✓
└── pyproject.toml               # Modern Python packaging ✓
```

## 🚀 Next Steps

### 1. Install Development Dependencies

```bash
cd healpyxel
pip install -e ".[dev,tdigest,duckdb]"
nbdev_install_hooks
```

### 2. Open Jupyter and Explore

```bash
jupyter notebook nbs/
```

**Start with:**
- `index.ipynb` - Package homepage
- `00_setup.ipynb` - Setup guide and test data exploration
- `90_example_streaming.ipynb` - Streaming workflow example

### 3. Build the Package

Once you start creating modules in notebooks:

```bash
nbdev_export    # Convert notebooks → Python modules
nbdev_test      # Run tests
nbdev_docs      # Generate documentation
nbdev_preview   # Preview docs locally
```

## 📝 Migration Roadmap

### Phase 1: Core Utilities (Priority: High)
- [ ] Create `00_core.ipynb` with shared functions
  - HEALPix validation
  - Statistics (MAD, robust_std)
  - Logging setup
  - File I/O utilities

### Phase 2: Main Modules (Priority: High)
- [ ] `01_sidecar.ipynb` - Convert `healpix_sidecar.py`
- [ ] `02_aggregate.ipynb` - Convert `healpix_aggregate.py`
- [ ] `03_accumulator.ipynb` - Convert `healpix_accumulator.py`
- [ ] `04_finalize.ipynb` - Convert `healpix_finalize.py`

### Phase 3: CLI & Integration (Priority: Medium)
- [ ] `05_cli.ipynb` - CLI entry points
- [ ] Test all CLI commands
- [ ] Validate with test data

### Phase 4: Documentation & Examples (Priority: Medium)
- [ ] `91_example_batch.ipynb` - Batch processing example
- [ ] `92_example_validation.ipynb` - Validation workflow
- [ ] Add doctests to all functions
- [ ] Create comprehensive README

### Phase 5: Publishing (Priority: Low)
- [ ] Setup GitHub repository
- [ ] Configure GitHub Actions CI/CD
- [ ] Publish to PyPI
- [ ] Setup documentation hosting

## 🔧 nbdev Key Directives

Use these in notebook cells:

```python
#| default_exp module_name    # Define which module this notebook creates
#| export                      # Export this cell to the module
#| hide                        # Hide cell from documentation
#| echo: false                 # Don't show code in docs (show output only)
```

## 📊 Test Data Usage

### Quick Test (5k observations)
```python
from pathlib import Path
import pandas as pd

test_data_dir = Path('test_data')
df = pd.read_parquet(test_data_dir / 'samples' / 'sample_5k.parquet')
```

### Streaming Test (10 batches)
```python
batches_dir = test_data_dir / 'batches'
for i in range(1, 11):
    batch_file = batches_dir / f'batch_{i:03d}.parquet'
    df = pd.read_parquet(batch_file)
    # Process incrementally...
```

### Validation Test
```python
# Load combined file for batch processing comparison
combined = pd.read_parquet(test_data_dir / 'validation' / 'combined_batch_001_003.parquet')
```

## 💡 Development Tips

1. **Start Small**: Begin with `00_core.ipynb` to establish patterns
2. **Test Often**: Run `nbdev_export` frequently to catch errors early
3. **Use Examples**: Each function should have usage examples in the notebook
4. **Write Tests**: Add assert statements to validate functionality
5. **Document**: nbdev shows code + markdown in docs, so explain thoroughly

## 📚 Resources

- [nbdev Documentation](https://nbdev.fast.ai/)
- [nbdev Tutorial](https://nbdev.fast.ai/tutorials/)
- [Example Projects](https://nbdev.fast.ai/examples.html)

## 🎯 Immediate Actions

1. **Install dependencies**:
   ```bash
   cd healpyxel
   pip install -e ".[dev,tdigest,duckdb]"
   nbdev_install_hooks
   ```

2. **Open Jupyter**:
   ```bash
   jupyter notebook nbs/
   ```

3. **Explore test data** in `00_setup.ipynb`

4. **Start migrating** with `00_core.ipynb` (create it based on common functions)

---

**Happy coding!** 🚀
