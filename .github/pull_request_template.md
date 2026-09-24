## Summary
- What changed and why?

## Linked issue
- Closes #

## Tests
- Tests added or changed, and what they cover.
- For a change to the house's behaviour: the scenario or benchmark that shows it (`uv run python tools/benchmark.py --tier smoke --compare`).

## Docs
- The pages under `docs/` this changes, or "no user-visible change".

## Validation
- [ ] `uv run ruff format --check . && uv run ruff check .` passes.
- [ ] `uv run mypy custom_components/powerplan/core` is clean.
- [ ] The fast suite passes (see `CONTRIBUTING.md`).
- [ ] New or changed behaviour is tested in Home Assistant, with redacted debug logs attached.
