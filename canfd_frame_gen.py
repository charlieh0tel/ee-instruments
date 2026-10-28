#!/usr/bin/env python

"""Play an ISO CAN FD frame on a Keysight InfiniiVision's built-in wavegen.

A stand-in bus for checking CAN FD masks (see canfd_eye_test) without a real
network: cable Gen Out to the scope input and play one frame repeatedly. The
frame has real bit stuffing, stuff count and CRC-17, and switches bit rate at
the BRS and CRC-delimiter sample points, so the scope's CAN FD decoder accepts
it and the BRS pulse has the width a real node would give it.

Levels are the differential voltage as a differential probe shows it: dominant
2 V and recessive 0 V, positive for H-L probing and negative for L-H.

--dip N drops the level to 0.6 V (or -0.6 V) for the middle fifth of data-phase
bit N (1 = ESI), inside the 0.5-0.9 V keep-out zone. It needs N to be a
dominant bit; ESI always is. Use it to check that the mask catches faults.

--bad-crc flips the last CRC bit, so every frame is malformed: canfd_monitor
should count them all as spec errors and none as good frames.

Loopback eye test, in this order: canfd_eye_test's *RST would stop the
wavegen, and *RST during a running eye test has wedged the scope.
  R=TCPIP::a-mx4054a-10545.local::5025::SOCKET
  canfd_frame_gen.py 1M 2M --sp 75 --dsp 75 --polarity H-L --scope $R
  canfd_eye_test.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 --polarity H-L \\
      --scope $R --no-reset --impedance 1M --probe 1 --start
  canfd_frame_gen.py 1M 2M --off --scope $R

Other examples:
  canfd_frame_gen.py 1M 2M --dip 1 --scope TCPIP::a-mx4054a-10545.local::5025::SOCKET
  canfd_frame_gen.py 1M 2M --off --scope TCPIP::a-mx4054a-10545.local::5025::SOCKET
  canfd_frame_gen.py 1M 2M          # just print the frame
"""

import argparse
import math
import sys

import pyvisa

from canfd_eye_test import parse_rate
from keysight_mso import KeysightMSO

CRC17_POLY = 0x1685B  # x^17+x^16+x^14+x^13+x^11+x^6+x^4+x^3+x+1, x^17 implicit
GRAY = [0b000, 0b001, 0b011, 0b010, 0b110, 0b111, 0b101, 0b100]
DLC_LEN = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
MAX_POINTS = 4000  # 3940 is known to work on a 4054A; the real limit may be higher
IDLE_BITS = 20
DOMINANT_V = 2.0
DIP_V = 0.6


def bits(value, n):
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def crc17(stream):
    crc = 1 << 16
    for b in stream:
        nxt = b ^ ((crc >> 16) & 1)
        crc = (crc << 1) & 0x1FFFF
        if nxt:
            crc ^= CRC17_POLY
    return crc


def frame_bits(can_id, data):
    """Transmitted bits (1 = recessive) of a base-format CAN FD frame with BRS,
    each tagged with its timing: 'n' nominal, 'd' data, 'brs', 'crcdel'."""
    if len(data) not in DLC_LEN or len(data) > 16:
        raise ValueError("data length must be a CAN FD DLC size up to 16 (CRC-17)")
    raw = [0] + bits(can_id, 11) + [0, 0, 1, 0, 1, 0]  # ..RRS IDE FDF res BRS ESI
    raw += bits(DLC_LEN.index(len(data)), 4)
    for byte in data:
        raw += bits(byte, 8)
    tags = ["n"] * 16 + ["brs"] + ["d"] * (len(raw) - 17)

    # Dynamic stuffing, SOF through data field.
    out, out_tags, stuffed = [], [], 0
    run_bit, run = None, 0
    for b, t in zip(raw, tags, strict=True):
        out.append(b)
        out_tags.append(t)
        run = run + 1 if b == run_bit else 1
        run_bit = b
        if run == 5:
            out.append(1 - b)
            out_tags.append("n" if t == "n" else "d")
            stuffed += 1
            run_bit, run = 1 - b, 1

    # Stuff count (Gray code + even parity) and CRC, which covers the dynamic
    # stuff bits and the stuff count; then fixed stuff bits before the stuff
    # count and after every 4 bits.
    sc = bits(GRAY[stuffed % 8], 3)
    sc.append(sum(sc) % 2)
    for i, b in enumerate(sc + bits(crc17(out + sc), 17)):
        if i % 4 == 0:
            out.append(1 - out[-1])
            out_tags.append("d")
        out.append(b)
        out_tags.append("d")

    # CRC delimiter, ACK slot (driven dominant by a receiver), ACK delim, EOF.
    out += [1, 0, 1] + [1] * 7
    out_tags += ["crcdel"] + ["n"] * 9
    return out, out_tags


def durations_ps(nominal, data, sp, dsp):
    """Bit durations per timing tag, in integer picoseconds."""
    t_n, t_d = 1e12 / nominal, 1e12 / data
    dur = {
        "n": t_n,
        "d": t_d,
        "brs": sp * t_n + (1 - dsp) * t_d,
        "crcdel": dsp * t_d + (1 - sp) * t_n,
    }
    out = {k: round(v) for k, v in dur.items()}
    if any(abs(out[k] - v) > 0.01 for k, v in dur.items()):
        raise ValueError("bit times are not whole picoseconds")
    return out


def waveform(fb, tags, dur, dip_bit=None):
    """Sample values (1 dominant, -1 recessive) and the sample period in ps.

    The period is the coarsest that puts every edge exactly on a sample,
    halved while there is room, so --dip has some resolution.
    """
    period = math.gcd(*dur.values())
    total = sum(dur[t] for t in tags) + IDLE_BITS * dur["n"]
    if total // period > MAX_POINTS:
        raise ValueError(f"needs {total // period} points, more than {MAX_POINTS}")
    while total // (period // 2) <= MAX_POINTS and period % 2 == 0:
        period //= 2

    values = []
    data_bit = 0
    for b, t in zip(fb, tags, strict=True):
        n = dur[t] // period
        level = -1.0 if b else 1.0
        seg = [level] * n
        if t == "d":
            data_bit += 1
            if data_bit == dip_bit:
                if b:
                    raise ValueError(f"data-phase bit {dip_bit} is recessive")
                dip = 2 * DIP_V / DOMINANT_V - 1
                for i in range(2 * n // 5, 3 * n // 5):
                    seg[i] = dip
        values += seg
    values += [-1.0] * (IDLE_BITS * dur["n"] // period)
    return values, period


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("nominal", type=parse_rate, help="nominal bit rate, e.g. 1M")
    ap.add_argument("data", type=parse_rate, help="data-phase bit rate, e.g. 2M")
    ap.add_argument(
        "--sp", type=float, default=75.0, help="nominal sample point, %% (default 75)"
    )
    ap.add_argument(
        "--dsp", type=float, default=75.0, help="data sample point, %% (default 75)"
    )
    ap.add_argument(
        "--polarity",
        choices=["L-H", "H-L"],
        default="L-H",
        help="L-H = dominant -2 V, H-L = dominant +2 V (default L-H)",
    )
    ap.add_argument(
        "--id", type=lambda s: int(s, 0), default=0x123, help="11-bit ID (0x123)"
    )
    ap.add_argument(
        "--payload",
        type=bytes.fromhex,
        default=bytes(range(1, 9)),
        help="data bytes as hex (default 0102030405060708)",
    )
    ap.add_argument(
        "--dip", type=int, metavar="N", help="fault: dip in data-phase bit N (1=ESI)"
    )
    ap.add_argument(
        "--bad-crc", action="store_true", help="fault: flip the last CRC bit"
    )
    ap.add_argument("--scope", metavar="RESOURCE", help="play it on this scope")
    ap.add_argument(
        "--off",
        action="store_true",
        help="with --scope, stop any running eye test, then turn the wavegen off",
    )
    args = ap.parse_args()

    if not 0 <= args.id < 0x800:
        ap.error("--id must be 11 bits")
    if not (0 < args.sp < 100 and 0 < args.dsp < 100):
        ap.error("sample points must be between 0 and 100 %")
    if args.off and not args.scope:
        ap.error("--off needs --scope")

    if args.off:
        with KeysightMSO(pyvisa.ResourceManager("@py"), args.scope) as scope:
            # Eye test off first, as canfd_eye_test does before its *RST.
            scope.stop_mask_test()
            scope.wgen_off()
        print("mask test off, wavegen off")
        return 0

    try:
        fb, tags = frame_bits(args.id, args.payload)
        if args.bad_crc:
            # The last data-phase bit, just before the CRC delimiter.
            fb[tags.index("crcdel") - 1] ^= 1
        dur = durations_ps(args.nominal, args.data, args.sp / 100, args.dsp / 100)
        values, period = waveform(fb, tags, dur, args.dip)
    except ValueError as e:
        ap.error(str(e))

    print("".join(str(b) for b in fb))
    print("".join(t[0] for t in tags))
    print(
        f"{len(fb)} bits, BRS {dur['brs'] / 1e3:g} ns, "
        f"CRC delimiter {dur['crcdel'] / 1e3:g} ns, "
        f"{len(values)} points at {period / 1e3:g} ns"
    )

    if args.scope:
        sign = 1 if args.polarity == "H-L" else -1
        if sign < 0:
            values = [-v for v in values]
        low, high = sorted((0.0, sign * DOMINANT_V))
        frequency = 1e12 / (len(values) * period)
        with KeysightMSO(pyvisa.ResourceManager("@py"), args.scope) as scope:
            scope.wgen_arb(values, frequency, low, high)
        print(f"playing on {args.scope} at {frequency:.2f} Hz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
