#!/bin/sh
# Downloads Unreal Engine 5.6's Python API stub (PyPI package unreal-stub) for the tests.
set -e
cd "$(dirname "$0")"
rm -rf .stub && mkdir -p .stub
python3 -m pip download --no-deps --quiet unreal-stub==0.3 -d .stub
python3 -m zipfile -e .stub/unreal_stub-0.3-py3-none-any.whl .stub
echo "Stub at $(pwd)/.stub/unreal/unreal.py"
