# CI/CD

GitHub Actions only executes workflows from `.github/workflows/`, so the actual pipeline lives there: [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). This folder is for CI/CD-related documentation and any supporting configuration (branch protection notes, deploy scripts invoked by workflows, etc.) that isn't itself a workflow file.

Current pipeline (`ci.yml`): on every PR and push to `main`, spins up a Postgres service container and runs `ruff check`, `mypy`, and `pytest` for `apps/api`.
