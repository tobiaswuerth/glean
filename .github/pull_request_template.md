## What and why

<!-- What changes, and what problem it solves. Link the issue if there is one. -->

## Checklist

- [ ] Tests were written first, and bug fixes come with a regression test
- [ ] `uv run pytest --cov` passes with 100% line and branch coverage
- [ ] `uvx pre-commit run --all-files` passes
- [ ] Changes to the agent's prompts or sandbox were tried on real items against a test vault (`experiments/`)
- [ ] User-visible changes are noted under "Unreleased" in `CHANGELOG.md`
