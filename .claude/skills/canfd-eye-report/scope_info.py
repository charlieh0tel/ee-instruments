#!/usr/bin/env python

"""Print the scope's identity and mask-test state as JSON.

Both queries answer even while an eye test runs, so this is safe over USB.
Usage: scope_info.py RESOURCE
"""

import json
import sys
from pathlib import Path

import pyvisa

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from keysight_mso import KeysightMSO  # noqa: E402


def main(argv):
    with KeysightMSO(pyvisa.ResourceManager("@py"), argv[1]) as scope:
        idn = scope.inst.query("*IDN?").strip()
        mask_on = bool(int(scope.inst.query(":MTESt:ENABle?")))
    make, model, serial, firmware = (idn.split(",") + [""] * 4)[:4]
    info = {
        "resource": argv[1],
        "idn": idn,
        "make": make.strip(),
        "model": model.strip(),
        "serial": serial.strip(),
        "firmware": firmware.strip(),
        "mask_test_on": mask_on,
    }
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
