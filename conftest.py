"""Ensure the repo root is importable so `import core` works under pytest
regardless of rootdir handling. (For plain `unittest`, run from the repo root
with `-t .` which already adds it to sys.path.)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
