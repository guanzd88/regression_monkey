#!/usr/bin/env bash
set -euo pipefail
python -m pip install -U build twine
rm -rf dist build *.egg-info || true
python -m build
python -m twine check dist/*
echo "Upload where? 1=TestPyPI 2=PyPI"
read -r ans
if [ "$ans" = "1" ]; then
  python -m twine upload -r testpypi dist/*
else
  python -m twine upload dist/*
fi
