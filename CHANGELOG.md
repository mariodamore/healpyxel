# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Convert Python scripts to nbdev notebooks
- Implement Python API
- Add comprehensive tests
- Setup GitHub Actions CI/CD
- Complete API documentation

## [0.1.0] - 2025-12-11

### Added
- Initial package structure with nbdev
- Test data suite (59MB, 15 files)
- Example notebooks:
  - `index.ipynb` - Package homepage
  - `00_setup.ipynb` - Setup guide and test data exploration
  - `90_example_streaming.ipynb` - Streaming workflow example
- Original scripts ready for migration:
  - `healpix_sidecar.py` - HEALPix cell assignment
  - `healpix_aggregate.py` - Batch aggregation
  - `healpix_accumulator.py` - Streaming accumulation
  - `healpix_finalize.py` - Statistics finalization
- Development tools:
  - Makefile with common tasks
  - Documentation generation with nbdev
  - Git repository initialized
- Documentation:
  - README.md (from index.ipynb)
  - QUICKSTART.md
  - PACKAGE_AUDIT.md

### Infrastructure
- Python package structure with setuptools
- Modern pyproject.toml configuration
- nbdev settings.ini configuration
- Apache 2.0 license

[Unreleased]: https://github.com/mariodamore/healpyxel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mariodamore/healpyxel/releases/tag/v0.1.0
