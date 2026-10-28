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

    def open(self, attempts=3):
        assert self.inst is None
        # Raw SCPI (TCPIP::host::5025::SOCKET) needs explicit termination.
        kwargs = {}
        if self.resource_name.upper().endswith("::SOCKET"):
            kwargs = {"read_termination": "\n", "write_termination": "\n"}
        # The 4054A's VXI-11 server now and then refuses connections, for
        # seconds at a time; raw SCPI on port 5025 has been reliable.
        for attempt in range(attempts):
            try:
                self.inst = self.resource_manager.open_resource(
                    self.resource_name, **kwargs
                )
                # pyvisa's 2 s default is tight for slow replies, and over
                # USB a timed-out query leaves the scope's USB interface dead
                # until it is rebooted.
                self.inst.timeout = 30000
                return self
            except pyvisa.errors.VisaIOError:
                if attempt == attempts - 1:
                    raise
                time.sleep(2)

    def close(self):
        if self.inst:
            self.inst.close()
        self.inst = None

    def get_png(self, timeout_ms=20000):
        assert self.inst
        self.inst.write(":HARDcopy:INKSaver OFF")
        # Slower than pyvisa's 2 s default while a mask test is running.
        saved, self.inst.timeout = self.inst.timeout, timeout_ms
        try:
            image = self.inst.query_binary_values(":DISP:DATA? PNG,COL", datatype="B")
        finally:
            self.inst.timeout = saved
        return bytes(image)

    def errors(self):
        """Drain the error queue; returns the non-zero entries."""
        assert self.inst
        found = []
        while True:
            err = self.inst.query(":SYSTem:ERRor?").strip()
            if err.startswith("+0,"):
                return found
            found.append(err)

    def probe_info(self, channel):
        """(probe ID, attenuation, input impedance) as the scope reports them."""
        assert self.inst
        assert 1 <= channel <= 4
        return (
            self.inst.query(f":CHANnel{channel}:PROBe:ID?").strip(),
            float(self.inst.query(f":CHANnel{channel}:PROBe?")),
            self.inst.query(f":CHANnel{channel}:IMPedance?").strip(),
        )

    def stop_mask_test(self):
        """Turn mask testing off, which also exits a CAN FD eye test sequence.

        Do this before *RST or before stopping the traffic: *RST sent while a
        sequence was running has wedged the scope until power-cycled.

        Over USB, exiting a running eye test with real traffic never answers
        and wedges the scope too, so there it raises instead: turn the test
        off on the front panel. Over the network it has worked.
        """
        assert self.inst
        if not int(self.inst.query(":MTESt:ENABle?")):
            return
        if self.resource_name.upper().startswith("USB"):
            raise RuntimeError(
                "mask test is on: over USB, turning it off can wedge the scope "
                "until power-cycled. Turn it off on the front panel (Analyze > "
                "Features: Mask) and run again."
            )
        self.inst.write(":MTESt:ENABle 0")
        self.inst.query("*OPC?")

    def reset(self):
        """Default Setup (*RST): also clears measurements, decode and wavegen."""
        assert self.inst
        self.inst.write("*RST")
        self.inst.query("*OPC?")

    def solo_channel(self, channel):
        """Display only this analog channel: others and the digital pods off."""
        assert self.inst
        assert 1 <= channel <= 4
        for n in range(1, 5):
            self.inst.write(f":CHANnel{n}:DISPlay {int(n == channel)}")
        for pod in (1, 2):
            self.inst.write(f":POD{pod}:DISPlay 0")

    def prepare_channel(self, channel, reset=True, impedance=None, attenuation=None):
        """Get to a known state with only this channel on, as Keysight's CAN FD
        app note starts: mask test off (exiting any eye test, so *RST can't
        wedge the scope), Default Setup unless reset is False, channel solo,
        then input impedance ("FIFT" or "ONEM") and probe attenuation if
        given, for a probe the scope can't identify: *RST leaves 1 MOhm, 1:1.

        Returns probe_info() from before the reset, with the settings asked
        for; pass it to check_probe() once set up, so an AutoProbe's own
        settings can't be silently undone.
        """
        self.stop_mask_test()
        probe = self.probe_info(channel)
        if reset:
            self.reset()
        self.solo_channel(channel)
        if impedance is not None:
            self.inst.write(f":CHANnel{channel}:IMPedance {impedance}")
            probe = (*probe[:2], impedance)
        if attenuation is not None:
            self.inst.write(f":CHANnel{channel}:PROBe {attenuation:g}")
            probe = (probe[0], float(attenuation), probe[2])
        return probe

    def check_probe(self, channel, before):
        if (after := self.probe_info(channel)) != before:
            raise RuntimeError(
                f"CHAN{channel} probe settings changed: {before} -> {after}"
            )

    def setup_canfd_decode(self, channel, nominal, data, sp, dsp, level, trigger):
        """CAN FD (ISO) decode on serial bus 1, differential probe on channel.

        sp/dsp are sample points in percent. level is the decode threshold in
        volts: its sign picks dominant-high (DIFH) or dominant-low (DIFL)
        probing. trigger is a :SBUS1:CAN:TRIGger value, e.g. ALLerrors.
        """
        assert self.inst
        assert 1 <= channel <= 4
        src = f"CHAN{channel}"
        self.errors()  # drop stale entries, e.g. from an earlier session
        # As the eye-test masks do: 600 mV/div, dominant level on screen.
        # *RST leaves 5 V/div, times the probe ratio: at 10:1 an ADC step is
        # ~1.6 V and the decoder sees nothing.
        self.inst.write(f":CHANnel{channel}:SCALe 0.6")
        self.inst.write(f":CHANnel{channel}:OFFSet {0.6 if level > 0 else -0.6}")
        # The decoder thresholds at the channel's trigger level.
        self.inst.write(":TRIGger:MODE EDGE")
        self.inst.write(f":TRIGger:EDGE:SOURce {src}")
        self.inst.write(f":TRIGger:EDGE:LEVel {level}")
        for cmd in (
            ":SBUS1:MODE CAN",
            f":SBUS1:CAN:SOURce {src}",
            f":SBUS1:CAN:SIGNal:DEFinition {'DIFH' if level > 0 else 'DIFL'}",
            f":SBUS1:CAN:SIGNal:BAUDrate {int(nominal)}",
            f":SBUS1:CAN:SIGNal:FDBaudrate {int(data)}",
            f":SBUS1:CAN:SAMPlepoint {sp}",
            f":SBUS1:CAN:FDSPoint {dsp}",
            ":SBUS1:CAN:FDSTandard ISO",
            ":SBUS1:DISPlay 1",
            ":LISTer:DISPlay SBUS1",
            ":TRIGger:MODE SBUS1",
            f":SBUS1:CAN:TRIGger {trigger}",
            ":TRIGger:SWEep NORMal",
            ":RUN",
        ):
            self.inst.write(cmd)
        if errs := self.errors():
            raise RuntimeError(f"CAN FD decode setup failed: {errs}")

    def can_counts(self):
        """Serial bus 1 CAN totals since the last reset_can_counts().

        Counted in hardware, so they cover all traffic, not just what was
        captured: frames, error frames, spec errors (malformed frames: CRC,
        stuff, form...), overload frames, utilization in %. A frame with a
        bad CRC counted as both a frame and a spec error; one with a flipped
        data bit (bad CRC and stuff count) only as a spec error.
        """
        assert self.inst
        q = self.inst.query
        return {
            "frames": int(q(":SBUS1:CAN:COUNt:TOTal?")),
            "error_frames": int(q(":SBUS1:CAN:COUNt:ERRor?")),
            "spec_errors": int(q(":SBUS1:CAN:COUNt:SPEC?")),
            "overload": int(q(":SBUS1:CAN:COUNt:OVERload?")),
            "utilization": float(q(":SBUS1:CAN:COUNt:UTILization?")),
        }

    def reset_can_counts(self):
        assert self.inst
        self.inst.write(":SBUS1:CAN:COUNt:RESet")

    def get_lister(self, timeout_ms=20000):
        """The decode lister for the current acquisition, as CSV text.

        Only ask after something has triggered: with no acquisition the query
        never answers.
        """
        assert self.inst
        saved, self.inst.timeout = self.inst.timeout, timeout_ms
        try:
            data = self.inst.query_binary_values(":LISTer:DATA?", datatype="B")
        finally:
            self.inst.timeout = saved
        return bytes(data).decode("ascii", errors="replace")

    def load_mask(self, mask_text, channel=1):
        """Load .msk file contents over SCPI, as recalling it from USB would.

        Front-panel recall substitutes the source channel for %d in the setup
        section; over SCPI that is ours to do. Raises on any queued error.
        """
        assert self.inst
        assert 1 <= channel <= 4
        body = mask_text.replace("\r\n", "\n").replace("%d", str(channel))
        self.errors()
        self.inst.write_binary_values(":MTESt:DATA ", body.encode("ascii"), "B")
        self.inst.query("*OPC?")
        if errs := self.errors():
            raise RuntimeError(f"mask load failed: {errs}")

    def get_mask(self):
        """Currently loaded mask, as .msk file text."""
        assert self.inst
        data = self.inst.query_binary_values(":MTESt:DATA?", datatype="B")
        return bytes(data).decode("ascii")

    def mask_counts(self):
        """(waveforms tested, waveforms failed, seconds tested) so far."""
        assert self.inst
        return (
            int(self.inst.query(":MTESt:COUNt:WAVeforms?")),
            int(self.inst.query(":MTESt:COUNt:FWAVeforms?")),
            float(self.inst.query(":MTESt:COUNt:TIME?")),
        )

    def reset_mask_counts(self):
        assert self.inst
        self.inst.write(":MTESt:COUNt:RESet")

    def start_canfd_eye(self, data_bitrate):
        """Start the CAN FD eye-diagram test sequence on the loaded mask.

        Send it on its own, never inside the :MTESt:DATA block: as part of the
        mask's setup section it wedged the scope until power-cycled. Sent alone
        with no traffic it just tests nothing.
        """
        assert self.inst
        # It does nothing with mask testing off, e.g. after stop_mask_test().
        self.inst.write(":MTESt:ENABle 1")
        # Spelled as in Keysight's .msk files; the long form is undocumented.
        self.inst.write(f":MTES:TSEQ:CAN:FD {int(data_bitrate)}")

    def wgen_arb(self, values, frequency, low, high):
        """Play values (each -1..1, mapped onto low..high volts) on the built-in
        waveform generator, repeating at frequency, into a high-Z load."""
        assert self.inst
        assert all(-1 <= v <= 1 for v in values)
        self.inst.write(":WGEN:FUNCtion ARBitrary")
        self.inst.write(":WGEN:ARBitrary:DATA " + ",".join(f"{v:g}" for v in values))
        self.inst.query("*OPC?")
        self.inst.write(f":WGEN:FREQuency {frequency:.6f}")
        self.inst.write(":WGEN:OUTPut:LOAD ONEM")
        self.inst.write(f":WGEN:VOLTage:HIGH {high}")
        self.inst.write(f":WGEN:VOLTage:LOW {low}")
        self.inst.write(":WGEN:OUTPut 1")
        if errs := self.errors():
            raise RuntimeError(f"wavegen setup failed: {errs}")

    def wgen_off(self):
        assert self.inst
        self.inst.write(":WGEN:OUTPut 0")

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
