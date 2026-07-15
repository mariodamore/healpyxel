# Current Status

## NOW
- [ ] **83_example_accumulation.py** — Make the accumulation validation notebook self-contained so it can leave ADR-011 exclusion from published docs
- [ ] **TDigest accuracy documentation** — Add ~1e-3 accuracy bound note to README/notebook for downstream users comparing streaming vs batch percentile output

## NEXT
- [ ] **Scale testing** — Validate accumulator performance at 50M observation scale
- [ ] **PSF Phase A implementation** — Two-pass workflow, Angular PSF evaluation, Configurable subgrid NSIDE, Generalized parent-child mapping
- [ ] **FITS Export** — Enhanced FITS format support for planetary science archives

## KNOWN_ISSUES

### Bugs
- None currently documented

### Technical Debt
- **Legacy docs** — `PROJECT_PLAN.md` and `IMPLEMENTATION_PLAN.md` should be archived
- **Float-to-int conversions** — Several locations in geospatial.py use `float(x)` where `x` is already float

_Last updated: 2026-07-15_
