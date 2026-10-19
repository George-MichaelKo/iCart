# Contributing to iCart

This project is currently implementing the local core. Work for the current development effort targets `gmk`; do not push to another branch without an explicit request.

1. Keep domain logic independent of integration libraries. Add external capabilities behind application ports and wire them in `bootstrap.py`.
2. Include behavior tests for meaningful changes, especially money, authorization, provider normalization, and retries. Pair rejection cases with valid cases.
3. Do not place credentials, real personal data, or paid provider calls in fixtures or tests.
4. Run `PYTHONPATH=src python3 -m unittest discover -s tests -v` and `git diff --check` before committing.
5. Update the README's implemented/planned distinction and document limitations. Do not describe mock-provider tests as live integration validation.
6. Use one file per commit with a short message describing that file's change, as requested by the project owner. The complete commit series is the tested unit when files depend on one another; intermediate commits may not run independently. A new model or SDK must not bypass the tool gateway or silently enable network access.
7. For database changes, run the PostgreSQL integration suite against the dedicated `icart_test` database. Never edit an applied migration; add a new migration instead. Do not put real user data in the local development database.

The source layout is documented in [architecture.md](docs/architecture.md). The reproducible validation strategy is in [evaluation.md](docs/evaluation.md).
