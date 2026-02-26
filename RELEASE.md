# Release Guide

This project is published to both TestPyPI and PyPI. The steps below assume you are on a clean
`main` branch and have updated `pyproject.toml`, `CHANGELOG.md`, and `README.md`.

## 1. Prerequisites
- Python 3.14+
- `pip install build twine`
- PyPI and TestPyPI API tokens (`__token__` username)

## 2. Version bump
1. Update `pyproject.toml` → `version`.
2. Update `CHANGELOG.md` with the release notes.
3. Commit the changes.

## 3. Build distributions
```bash
rm -rf dist/
python -m build
```
This produces `dist/*.tar.gz` and `dist/*.whl`.

## 4. Upload to TestPyPI
```bash
python -m twine upload --repository testpypi dist/*
```
- Username: `__token__`
- Password: `pypi-AgENdGVzdC5weXBpLm9yZwIk...` (replace with your token)

Verify installation from TestPyPI:
```bash
python -m pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple regression_monkey==<version>
```

## 5. Upload to PyPI
```bash
python -m twine upload dist/*
```
- Username: `__token__`
- Password: `pypi-AgEIcHlwaS5vcmcCJ...` (replace with your production token)

## 6. Tag and push
```bash
git tag v<version>
git push origin main --tags
```

## 7. Post-release checklist
- Create a GitHub release with the changelog entry.
- Announce internally (Slack/Teams) with installation instructions.
- Update any downstream projects that pin the previous version.
