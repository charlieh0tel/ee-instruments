#!/usr/bin/env python

import sys
import time
from enum import StrEnum, auto

import pyvisa


class Measurement(StrEnum):
    DUTYCYCLE = auto()
    FREQUENCY = auto()
    VAMPLITUDE = auto()
    VAVERAGE = auto()
    VBASE = auto()
    VMAX = auto()
    VMIN = auto()
    VPP = auto()
    VRMS = auto()
    VTOP = auto()


class KeysightMSO:
    def __init__(self, resource_manager, resource_name):
        self.resource_manager = resource_manager
        self.resource_name = resource_name
        self.inst = None

    def __enter__(self, *args):
        return self.open(*args)

    def __exit__(self, *args):
        self.close()

    def open(self):
        assert self.inst is None
        self.inst = self.resource_manager.open_resource(self.resource_name)
        return self

    def close(self):
        if self.inst:
            self.inst.close()
        self.inst = None

    def get_png(self):
        assert self.inst
        self.inst.write(":HARDcopy:INKSaver OFF")
        image = self.inst.query_binary_values(":DISP:DATA? PNG,COL", datatype="B")
        return bytes(image)

    def force_trigger(self):
        assert self.inst
        self.inst.write(":TRIG:FORC")

    def single(self):
        assert self.inst
        self.inst.write(":SING")

    def stop(self):
        assert self.inst
        self.inst.write(":STOP")

    def measure_channel(self, channel, measure: Measurement, *options):
        assert self.inst
        assert 1 <= channel <= 4
        options = list(options) + [f"CHAN{channel}"]
        options_string = ",".join(options)
        query = f":MEAS:{measure}? {options_string}"
        # print(query)
        v = float(self.inst.query(query))
        if v <= -9.9e37:
            return float("-inf")
        elif v >= 9.9e37:
            return float("+inf")
        else:
            return v


def main(argv):
    rm = pyvisa.ResourceManager("@py")
    # resources = rm.list_resources()
    # print(resources)

    resource_name = argv[1] if len(argv) >= 2 else "TCPIP::a-mx4054a-10545.local::INSTR"

    with KeysightMSO(rm, resource_name) as scope:
        print("stopping")
        scope.stop()
        print("single")
        scope.single()
        time.sleep(0.25)
        print(f"DUTYCYCLE(1) = {scope.measure_channel(1, Measurement.DUTYCYCLE):.3f}")
        print(f"FREQUENCY(1) = {scope.measure_channel(1, Measurement.FREQUENCY):.3f}")
        print(f"VAMPLITUDE(1) = {scope.measure_channel(1, Measurement.VAMPLITUDE):.3f}")
        print(f"VAVERAGE(1) = {scope.measure_channel(1, Measurement.VAVERAGE):.3f}")
        print(f"VBASE(1) = {scope.measure_channel(1, Measurement.VBASE):.3f}")
        print(f"VMAX(1) = {scope.measure_channel(1, Measurement.VMAX):.3f}")
        print(f"VMIN(1) = {scope.measure_channel(1, Measurement.VMIN):.3f}")
        print(f"VPP(1) = {scope.measure_channel(1, Measurement.VPP):.3f}")
        vrms_ac = scope.measure_channel(1, Measurement.VRMS, "CYCLE", "AC")
        print(f"VRMS_AC(1) = {vrms_ac:.3f}")
        vrms_dc = scope.measure_channel(1, Measurement.VRMS, "CYCLE", "DC")
        print(f"VRMS_DC(1) = {vrms_dc:.3f}")
        print(f"VTOP(1) = {scope.measure_channel(1, Measurement.VTOP):.3f}")

        png = scope.get_png()
        path = "scope.png"
        with open(path, "wb") as f:
            f.write(png)
        print(f"wrote {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
