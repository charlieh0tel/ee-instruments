#!/usr/bin/env python

import sys

import pyvisa


class Keithley2015:
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

    def reset(self):
        self.write("*RST")

    def system_preset(self):
        self.write(":SYS:PRESET")

    def write(self, *args, **kwargs):
        assert self.inst
        self.inst.write(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self.inst.query(*args, **kwargs)


def main(argv):
    rm = pyvisa.ResourceManager("@py")

    resource_name = argv[1] if len(argv) >= 2 else "TCPIP::e5810a::gpib0,22::INSTR"

    with Keithley2015(rm, resource_name) as meter:
        meter.reset()
        meter.inst.timeout = 10.0 * 1000.0

        print(meter.query("*IDN?"))

        lpf_cutoff = None
        hpf_cutoff = 8000.0

        meter.write(":SENS:FUNC 'dist'")
        meter.write(":SENS:DIST:TYPE SINAD")
        meter.write(":SENS:DIST:SFIL NONE")
        meter.write(":SENS:DIST:FREQ:ACQUIRE")
        meter.write(":SENS:DIST:SFIL NONE")  # CCITT?
        meter.write(":SENS:DIST:RANG:AUTO ON")
        meter.write(":UNIT:DIST DB")
        if lpf_cutoff is not None:
            meter.write(f":SENS:DIST:LCO {int(lpf_cutoff)}")
            meter.write(":SENS:DIST:LCO:STATE ON")
        if hpf_cutoff is not None:
            meter.write(f":SENS:DIST:HCO {int(hpf_cutoff)}")
            meter.write(":SENS:DIST:HCO:STATE ON")
        while True:
            sinad_db = float(meter.query(":READ?"))
            freq_hz = float(meter.query(":SENS:DIST:FREQ?"))
            print(f"SINAD={sinad_db:10.3f} dB, f={freq_hz:10.3f} Hz")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
