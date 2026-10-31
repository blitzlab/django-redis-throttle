# Contributing

Thanks for helping out. Bug reports, docs fixes and new features are all welcome.

## Setup

```bash
git clone https://github.com/blitzlab/django-redis-throttle
cd django-redis-throttle
python -m venv .venv && source .venv/bin/activate
pip install -e . pytest pytest-django djangorestframework ruff
docker run -d -p 6379:6379 redis:7
```

## Before opening a pull request

```bash
pytest                                  # REDIS_URL defaults to redis://localhost:6379/15
ruff check . && ruff format --check .
```

- Add a test for every behaviour change. Algorithm tests use the `clock` fixture so they never sleep.
- If you touch a Lua script, add a case to `tests/test_algorithms.py` that checks the exact boundary (just before and at `retry_after`).
- Keep the public API in `redis_throttle/__init__.py` small and documented in the README.
- Add an entry under "Unreleased" in `CHANGELOG.md`.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `perf:`, `ci:`, `chore:`.

## Releasing (maintainers)

1. Update `__version__` in `src/redis_throttle/__init__.py` and move "Unreleased" in the changelog to the new version.
2. Tag and push: `git tag v0.2.0 && git push --tags`.
3. The release workflow builds, checks and publishes to PyPI through trusted publishing.
