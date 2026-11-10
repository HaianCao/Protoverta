# Contributing to Protoverta

Thanks for helping. Protoverta is a strict protocol adapter: a change must never
invent data, silently drop data, or forward state to a provider that did not
create it. When a feature cannot be preserved, reject it with a clear error.

## Development setup

Python 3.11 or newer. The runtime has no dependencies.

```powershell
python -m pip install -e ".[test,typing]"
python -m pytest
python -m ruff check src tests sdk_tests scripts
python -m ruff format --check .
python scripts/check_types.py
```

Documentation is built with Sphinx; warnings are errors:

```powershell
python -m pip install -r docs/requirements.txt
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Every name exported from `protoverta` needs a docstring; CI checks this.

## Pull requests

- Add sanitized fixtures and tests for **both directions** of any protocol
  change, and for the streaming and non-streaming paths.
- Never commit API keys, private conversations or real provider responses that
  contain personal data. Live provider checks are opt-in (`--allow-live`).
- Keep the two launchers in mind: modules shared verbatim by CodexApter and
  ClaudApter are checked by `scripts/check_launcher_sync.py`.
- Update `CHANGELOG.md` under "Unreleased" and the relevant page in `docs/`.
- CI runs on Windows and Ubuntu with Python 3.11, 3.13 and 3.14.

## Releases

Releases are cut by the maintainer; see [docs/releasing.md](https://github.com/HaianCao/Protoverta/blob/main/docs/releasing.md).
Every code change ships under a new version; published tags and assets are
never replaced.
