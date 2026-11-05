"""Import every module in the repository.

The dependency list in pyproject.toml is derived from what the code imports,
so importing everything is what verifies it; in particular the two modules
under rf_sensor_measurements are the only consumers of matplotlib and
pyarrow. Every module here is __main__-guarded, so importing them performs
no I/O.
"""

import importlib
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The drivers live at the repo root; the plotting scripts live in
# rf_sensor_measurements and import as top-level modules from inside it.
# [tool.pytest.ini_options] pythonpath puts both on sys.path.
SEARCH_DIRS = (ROOT, ROOT / "rf_sensor_measurements")

# Module names, one per .py file in SEARCH_DIRS.
MODULES = [path.stem for d in SEARCH_DIRS for path in sorted(d.glob("*.py"))]


@pytest.mark.parametrize("module", MODULES)
def test_import(module):
    importlib.import_module(module)
