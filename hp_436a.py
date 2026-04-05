#!/usr/bin/env python

from enum import IntEnum
import sys
import time

import vxi11


class HP436A:
    class Range(IntEnum):
        Auto = ord("9")
        Range1 = ord("1")  # Most sensitive
        Range2 = ord("2")
        Range3 = ord("3")
        Range4 = ord("4")
        Range5 = ord("5")  # Least sensitive

    class Mode(IntEnum):
        Watt = ord("A")
        dBRel = ord("B")
        dBRef = ord("C")
        dBm = ord("D")
        AutoZero = ord("Z")

    class CalFactor(IntEnum):
        Disable = ord("+")  # 100%
        FrontPanel = ord("-")

    class Rate(IntEnum):
        Hold = ord("H")
        TriggerWithSettling = ord("T")
        TriggerImmediate = ord("I")
        FreeRunMaxRate = ord("R")
        FreeRunWithSettling = ord("V")

    class StatusOutput(IntEnum):
        MeasurementValid = ord("P")
        WattsModeUnderRange = ord("Q")
        OverRange = ord("R")
        UnderRangeOndB = ord("S")
        AutoZeroNormalRange1 = ord("T")
        AutoZeroNormalOtherRanges = ord("U")
        AutoZeroError = ord("V")
        AutoZeroUnknownW = ord("W")

    class RangeOutput(IntEnum):
        Range1 = ord("I")  # Most sensitive
        Range2 = ord("J")
        Range3 = ord("K")
        Range4 = ord("L")
        Range5 = ord("M")  # Least sensitive

    class ModeOutput(IntEnum):
        Watt = ord("A")
        dBRel = ord("B")
        dbRef = ord("C")
        dBm = ord("D")

    def __init__(self, resource_name):
        self.resource_name = resource_name
        self.inst = None

    def __enter__(self, *args):
        return self.open(*args)

    def __exit__(self, *args):
        self.close()

    def open(self):
        assert self.inst is None
        self.inst = vxi11.Instrument(self.resource_name)
        self.inst.clear()
        return self

    def close(self):
        if self.inst:
            self.inst.local()
            self.inst.close()
        self.inst = None

    def _write_bytes(self, b):
        assert self.inst
        self.inst.write_raw(b)

    def _write_byte(self, b):
        self._write_bytes(bytes([b]))

    def _read(self):
        assert self.inst
        return self.inst.read_raw(num=14)

    def zero_meter(self, timeout_seconds=20.0):
        timeout_at = time.time() + timeout_seconds
        # Following Figure 3-8 of 436A Power Meter Operating and Service Manual.
        while True:
            if time.time() >= timeout_at:
                raise TimeoutError()
            self._write_bytes(b"Z1T")
            (_, _, _, _, mantissa, _) = self.read()
            if abs(mantissa) <= 2:
                break
        while True:
            if time.time() >= timeout_at:
                raise TimeoutError()
            self._write_bytes(b"9+AI")
            (_, status, _, _, _, _) = self.read()
            if status < self.StatusOutput.AutoZeroNormalRange1:
                break

    def read_with_settling_dBm(self, timeout_seconds=100.0):
        timeout_at = time.time() + timeout_seconds
        # Following Figure 3-8 of 436A Power Meter Operating and Service Manual.
        self._write_bytes(b"9D+V")
        (_, _, range_, _, mantissa, _) = self.read()
        while True:
            last_mantissa = mantissa
            if time.time() >= timeout_at:
                raise TimeoutError()
            self._write_bytes(b"9D+V")
            if range_ == self.RangeOutput.Range1:
                time.sleep(4.0)
            (value, _, range_, _, mantissa, _) = self.read()
            if abs(last_mantissa - mantissa) <= 1:
                return value

    def set_range(self, range: Range):
        self._write_byte(range.value)

    def set_mode(self, mode: Mode):
        self._write_byte(mode.value)

    def set_cal_factor(self, cal_factor: CalFactor):
        self._write_byte(cal_factor.value)

    def set_rate(self, rate: Rate):
        self._write_byte(rate.value)

    def read(self):
        return self.ParseMessage(self._read())

    @classmethod
    def ParseMessage(cls, b: bytes):
        try:
            assert len(b) == 14
            status = cls.StatusOutput(b[0])
            range_ = cls.RangeOutput(b[1])
            mode = cls.ModeOutput(b[2])
            assert b[8] == ord("E")
            assert b[9] == ord("-")
            mantissa = float(b[3:8])
            exponent = float(b[9:12])
            value = mantissa * (10**exponent)
            assert b[12] == ord("\r")
            assert b[13] == ord("\n")
            return (value, status, range_, mode, mantissa, exponent)
        except:
            print(f"failed to parse {b}", file=sys.stderr)
            raise


def main(argv):
    resource_name = argv[1] if len(argv) >= 2 else "tcpip::e5810a::gpib0,13::instr"

    with HP436A(resource_name) as pm:
        input("turn off power and hit return -> ")
        pm.zero_meter()
        input("turn on power and hit return -> ")
        pm.set_range(pm.Range.Auto)
        pm.set_mode(pm.Mode.dBm)
        for _ in range(10):
            dBm = pm.read_with_settling_dBm()
            print(dBm)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
