# How to publish to PyPI / TestPyPI

## 1) Build
```bash
python -m pip install -U build twine
python -m build
```

## 2) Upload to TestPyPI first
```bash
python -m twine upload --repository testpypi dist/*
# or: twine upload -r testpypi dist/*
```
Create an API token at https://test.pypi.org/manage/account/token/ and use it as the username:
- username: __token__
- password: pypi-AgENdGVzdC5weXBpLm9yZwIk...
(Your actual token string)

## 3) Install from TestPyPI to verify
```bash
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple reg_monkey==0.1.2
```

## 4) Upload to PyPI (production)
```bash
python -m twine upload dist/*
```
Use a real PyPI API token from https://pypi.org/manage/account/token/:
- username: __token__
- password: pypi-AgEIcHlwaS5vcmcCJ...

## 5) Tag the release
```bash
git tag v0.1.2
git push --tags
```
