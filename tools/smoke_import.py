"""Import every module in the repository.

The dependency list in pyproject.toml is derived from what the code imports, so
importing everything is what verifies it -- in particular the two modules under
rf_sensor_measurements, which are the only consumers of matplotlib and pyarrow.

There are no unit tests: the drivers talk to real instruments over GPIB/VISA, so
they cannot run in CI. That makes this the only executable check in the pipeline.
Every module here is __main__-guarded, so importing them performs no I/O.

Run from the repository root: `python tools/smoke_import.py`
"""

import importlib
import os
import pathlib
import sys

# matplotlib selects a backend at import time, so force the headless one before
# anything pulls in pyplot. Set here rather than as a CI env var because the
# shared workflow rejects a VAR=value prefix on the command it is given.
os.environ.setdefault("MPLBACKEND", "Agg")

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The drivers live at the repo root; the plotting scripts live in
# rf_sensor_measurements and import as top-level modules from inside it. Running
# this file from tools/ puts tools/ on sys.path, not either of these, so both
# have to be added explicitly.
SEARCH_DIRS = (ROOT, ROOT / "rf_sensor_measurements")


def main():
    for directory in SEARCH_DIRS:
        sys.path.insert(0, str(directory))

    count = 0
    for directory in SEARCH_DIRS:
        for path in sorted(directory.glob("*.py")):
            importlib.import_module(path.stem)
            print(f"ok {path.relative_to(ROOT)}")
            count += 1

    print(f"{count} modules imported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
