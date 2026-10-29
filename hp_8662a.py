#!/usr/bin/env python

import sys
import time

import vxi11


class HP8662A:
    def __init__(self, resource_name):
        self.resource_name = resource_name
        self.inst = None
        self.power_dBm = None
        self.first_ap_quirk = True

    def __enter__(self, *args):
        return self.open(*args)

    def __exit__(self, *args):
        self.close()

    def open(self):
        assert self.inst is None
        self.inst = vxi11.Instrument(self.resource_name)
        return self

    def close(self):
        if self.inst:
            self.inst.local()
            self.inst.close()
        self.inst = None

    def _write_string(self, s):
        assert self.inst
        # print(s)
        self.inst.write_raw(s.encode("ascii"))

    def reset(self):
        assert self.inst
        self.inst.clear()

    def system_preset(self):
        assert self.inst
        self._write_string("SP00\n")
        time.sleep(0.1)
        self._write_string("AO\n")

    def _check_frequency(self, frequency_hz: float):
        if 10e3 <= frequency_hz <= 1280e6:
            return
        raise ValueError(f"Frequency {frequency_hz} Hz out of range.")

    def set_frequency(self, frequency_hz: float):
        assert self.inst
        self._check_frequency(frequency_hz)
        self._write_string(f"FR{frequency_hz}HZ\n")

    def set_output(self, on: bool):
        assert self.inst
        if on:
            assert self.power_dBm is not None
            ap = f"AP{self.power_dBm:.1f}DM\n"
            self._write_string(ap)
            if self.first_ap_quirk:
                # First one doesn't take for some reason.
                self._write_string(ap)
                self.first_ap_quirk = False
        else:
            self._write_string("AO\n")

    def _check_power(self, power_dBm: float):
        if -129.9 <= power_dBm <= 19.9:
            return
        raise ValueError(f"Power {power_dBm} dBm out of range.")

    def set_power(self, power_dBm: float):
        self._check_power(power_dBm)
        self.power_dBm = power_dBm


class HP8663A(HP8662A):
    # Status byte bit set when a program code is rejected (manual 08663-90069,
    # "HP-IB Status Byte").
    STB_ENTRY_ERROR = 0x02

    def _check_frequency(self, frequency_hz: float):
        if 100e3 <= frequency_hz <= 2560e6:
            return
        raise ValueError(f"Frequency {frequency_hz} Hz out of range.")

    def status(self):
        """Returns the STATUS key message, "SS,HHH,M" (program code MS)."""
        assert self.inst
        self._write_string("MS\n")
        return self.inst.read().strip()

    def set_fm(self, rate_hz: float, deviation_hz: float):
        """
        Frequency modulates from the internal modulation oscillator.

        Written from the manual (08663-90069, "Modulation, Internal FM" and
        "Modulation, External FM"); not yet tried on an instrument.
        """
        assert self.inst
        # An entry error latches until both the status message and the status
        # byte have been read, so clear any left from before.
        self.status()
        self.inst.read_stb()
        # Deviation can only be entered in kHz.  MOD FREQ after FM selects the
        # internal oscillator even if FM was last external.
        self._write_string(f"FM{deviation_hz / 1e3:g}KZMF{rate_hz:g}HZ\n")
        # MS is answered only once the codes before it are processed, so the
        # status byte is current after it.
        status = self.status()
        if self.inst.read_stb() & self.STB_ENTRY_ERROR:
            raise ValueError(
                f"FM of {deviation_hz} Hz at {rate_hz} Hz rejected; status {status}."
            )


def main(argv):
    resource_name = argv[1] if len(argv) >= 2 else "tcpip::e5810a::gpib0,25::instr"

    with HP8662A(resource_name) as siggen:
        siggen.reset()
        siggen.system_preset()
        freq_hz = 150e6
        power_dBm = -22.0
        print(f"setting siggen for {freq_hz} Hz @ {power_dBm} dBm")
        siggen.set_frequency(freq_hz)
        try:
            print("power on")
            siggen.set_power(power_dBm)
            siggen.set_output(True)
            print("...")
            time.sleep(5)
            print("power off")
            siggen.set_output(False)
            time.sleep(5)
            print("power on")
            siggen.set_output(True)
            print("...")
            time.sleep(5)
        finally:
            print("power off")
            siggen.set_output(False)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
