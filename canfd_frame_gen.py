#!/usr/bin/env python

"""Play an ISO CAN FD frame on a Keysight InfiniiVision's built-in wavegen.

A stand-in bus for checking CAN FD masks (see canfd_eye_test) without a real
network: cable Gen Out to the scope input and play one frame repeatedly. The
frame has real bit stuffing, stuff count and CRC-17, and switches bit rate at
the BRS and CRC-delimiter sample points, so the scope's CAN FD decoder accepts
it and the BRS pulse has the width a real node would give it.

Levels are the differential voltage as a differential probe shows it: dominant
2 V and recessive 0 V, positive for H-L probing and negative for L-H.

--dip N[,N...] sets the level to 0.6 V (or -0.6 V) for the middle fifth of
data-phase bit N (1 = ESI), dominant or recessive, inside the 0.5-0.9 V
keep-out zone; --dip-at moves it within the bit. Use it to check that the mask
catches faults, and in which bits.

For checking the eye test's trigger: --brs sets the BRS pulse width, --edge
gives every dominant-to-recessive edge a linear ramp (a slow bus), and
--esi-recessive sends ESI recessive, as an error-passive node does.

--sender KEY=VALUE[,...] imitates a bus with several transmitters: each
occurrence adds one frame, played back to back with IDLE_BITS of idle after
each, with its own id, payload, sp, dsp (so BRS width), brs, edge, rise,
dominant and esi (0/1); missing keys come from the top-level options, and
--dip, --dip-at, --no-brs and --bad-crc apply to every frame. The wavegen's
high level is the largest dominant level; the others are scaled to it. All
frames share one sample period, so they must fit MAX_POINTS together, e.g.
  canfd_frame_gen.py 1M 2M --payload 01 \\
      --sender id=0x555,sp=80,dsp=80,edge=50,rise=100 \\
      --sender id=0x0F0,sp=70,dsp=80,dominant=1.5,edge=100,rise=50

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


def frame_bits(can_id, data, esi=0, brs=1):
    """Transmitted bits (1 = recessive) of a base-format CAN FD frame with BRS,
    each tagged with its timing: 'n' nominal, 'd' data, 'brs', 'crcdel'."""
    if len(data) not in DLC_LEN or len(data) > 16:
        raise ValueError("data length must be a CAN FD DLC size up to 16 (CRC-17)")
    raw = [0] + bits(can_id, 11) + [0, 0, 1, 0, brs, esi]  # ..RRS IDE FDF res BRS ESI
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
    if not brs:  # no rate switch: the whole frame at the nominal rate
        out_tags = ["n"] * len(out_tags)
    return out, out_tags


def durations_ps(nominal, data, sp, dsp, brs_ns=None):
    """Bit durations per timing tag, in integer picoseconds."""
    t_n, t_d = 1e12 / nominal, 1e12 / data
    dur = {
        "n": t_n,
        "d": t_d,
        "brs": sp * t_n + (1 - dsp) * t_d if brs_ns is None else brs_ns * 1e3,
        "crcdel": dsp * t_d + (1 - sp) * t_n,
    }
    out = {k: round(v) for k, v in dur.items()}
    if any(abs(out[k] - v) > 0.01 for k, v in dur.items()):
        raise ValueError("bit times are not whole picoseconds")
    return out


SENDER_KEYS = {
    "id": lambda s: int(s, 0),
    "payload": bytes.fromhex,
    "sp": float,
    "dsp": float,
    "brs": float,
    "edge": float,
    "rise": float,
    "dominant": float,
    "esi": int,
}


def parse_sender(spec):
    """--sender SPEC: comma-separated key=value, keys as in SENDER_KEYS."""
    out = {}
    for item in spec.split(","):
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or key not in SENDER_KEYS:
            raise argparse.ArgumentTypeError(
                f"{item!r}: want key=value with key one of {', '.join(SENDER_KEYS)}"
            )
        if key in out:
            raise argparse.ArgumentTypeError(f"{key} given twice")
        try:
            out[key] = SENDER_KEYS[key](value.strip())
        except ValueError as e:
            raise argparse.ArgumentTypeError(f"{item!r}: {e}") from None
    return out


def sample_period(frames, dip_at):
    """The coarsest period that puts every edge, dip boundary and ramp end of
    every frame exactly on a sample, halved while there is room, so --dip has
    some resolution. frames are dicts with tags, dur, edge_ps, rise_ps."""
    steps, total = [], 0
    for f in frames:
        dur = f["dur"]
        steps += [*dur.values(), f["edge_ps"], f["rise_ps"]]
        steps += [round(dur["d"] * pct / 100) for pct in dip_at]
        total += sum(dur[t] for t in f["tags"]) + IDLE_BITS * dur["n"]
    period = math.gcd(*steps)
    if total // period > MAX_POINTS:
        msg = f"needs {total // period} points, more than {MAX_POINTS}"
        if len(frames) > 1:
            msg = (
                f"{len(frames)} frames don't fit: at {period / 1e3:g} ns, the "
                "common grid of their bit times, edges and dips, they need "
                f"{total // period} points, more than {MAX_POINTS}; use shorter "
                "payloads, fewer senders, or bit times and edges on a coarser grid"
            )
        raise ValueError(msg)
    while total // (period // 2) <= MAX_POINTS and period % 2 == 0:
        period //= 2
    return period


def frame_values(f, period, dips, dip_at, full_v):
    """Sample values of one frame and the idle after it, on a scale where 1 is
    full_v (the largest dominant level) and -1 is recessive.

    A ramp of edge_ps runs linearly from dominant to recessive at the start of
    each recessive bit that follows a dominant one; rise_ps does the same for
    recessive to dominant.
    """
    dur = f["dur"]
    dip_ps = [round(dur["d"] * pct / 100) for pct in dip_at]
    level = {1: -1.0, 0: 2 * f["dominant_v"] / full_v - 1}  # by bit, 1 recessive
    dip_level = 2 * DIP_V / full_v - 1
    values = []
    data_bit = 0
    prev = 1  # idle before SOF is recessive
    for b, t in zip(f["fb"], f["tags"], strict=True):
        n = dur[t] // period
        seg = [level[b]] * n
        ramp = min((f["edge_ps"] if b else f["rise_ps"]) // period, n)
        if b != prev:  # ramp from the old level to the new one
            start, end = level[prev], level[b]
            for i in range(ramp):
                seg[i] = start + (end - start) * i / ramp
        if t == "d":
            data_bit += 1
            if data_bit in dips:
                for i in range(dip_ps[0] // period, dip_ps[1] // period):
                    seg[i] = dip_level
        values += seg
        prev = b
    values += [-1.0] * (IDLE_BITS * dur["n"] // period)
    return values


def waveform(frames, dips=(), dip_at=(40, 60)):
    """Sample values (1 the largest dominant level, -1 recessive) of the
    frames back to back, each followed by IDLE_BITS of idle, and the sample
    period in ps."""
    period = sample_period(frames, dip_at)
    full_v = max(f["dominant_v"] for f in frames)
    values = []
    for f in frames:
        values += frame_values(f, period, dips, dip_at, full_v)
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
        "--dip",
        type=lambda s: [int(n) for n in s.split(",")],
        default=[],
        metavar="N[,N...]",
        help="fault: 0.6 V level in data-phase bit N (1=ESI)",
    )
    ap.add_argument(
        "--dip-at",
        type=lambda s: [float(x) for x in s.split("-")],
        default=[40, 60],
        metavar="A-B",
        help="where the dip sits in the bit, %% of bit time (default 40-60)",
    )
    ap.add_argument("--brs", type=float, metavar="NS", help="BRS pulse width, ns")
    ap.add_argument(
        "--edge",
        type=float,
        default=0,
        metavar="NS",
        help="linear ramp on dominant-to-recessive edges, ns (default 0)",
    )
    ap.add_argument("--esi-recessive", action="store_true", help="error-passive sender")
    ap.add_argument(
        "--no-brs", action="store_true", help="BRS dominant: no rate switch"
    )
    ap.add_argument(
        "--rise",
        type=float,
        default=0,
        metavar="NS",
        help="linear ramp on recessive-to-dominant edges, ns (default 0)",
    )
    ap.add_argument(
        "--dominant",
        type=float,
        default=DOMINANT_V,
        metavar="V",
        help=f"dominant level, V (default {DOMINANT_V:g})",
    )
    ap.add_argument(
        "--bad-crc", action="store_true", help="fault: flip the last CRC bit"
    )
    ap.add_argument(
        "--sender",
        type=parse_sender,
        action="append",
        default=[],
        metavar="SPEC",
        help="add one frame from a sender with its own settings; repeat for "
        "several, played back to back. SPEC is key=value[,key=value...] with "
        "keys id, payload, sp, dsp, brs, edge, rise, dominant, esi (0/1); "
        "missing keys come from the options above",
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
    if len(args.dip_at) != 2 or not 0 <= args.dip_at[0] < args.dip_at[1] <= 100:
        ap.error("--dip-at needs A-B with 0 <= A < B <= 100")

    if args.off:
        with KeysightMSO(pyvisa.ResourceManager("@py"), args.scope) as scope:
            # Eye test off first, as canfd_eye_test does before its *RST.
            scope.stop_mask_test()
            scope.wgen_off()
        print("mask test off, wavegen off")
        return 0

    top = {
        "id": args.id,
        "payload": args.payload,
        "sp": args.sp,
        "dsp": args.dsp,
        "brs": args.brs,
        "edge": args.edge,
        "rise": args.rise,
        "dominant": args.dominant,
        "esi": int(args.esi_recessive),
    }
    senders = [top | spec for spec in args.sender] or [top]
    for i, s in enumerate(senders if args.sender else [], 1):
        if not 0 <= s["id"] < 0x800:
            ap.error(f"--sender {i}: id must be 11 bits")
        if not (0 < s["sp"] < 100 and 0 < s["dsp"] < 100):
            ap.error(f"--sender {i}: sample points must be between 0 and 100 %")
        if s["esi"] not in (0, 1):
            ap.error(f"--sender {i}: esi must be 0 or 1")
        if s["dominant"] <= 0:
            ap.error(f"--sender {i}: dominant must be above 0 V")

    try:
        frames = []
        for s in senders:
            fb, tags = frame_bits(s["id"], s["payload"], s["esi"], int(not args.no_brs))
            if args.bad_crc:
                # The last data-phase bit, just before the CRC delimiter.
                fb[tags.index("crcdel") - 1] ^= 1
            dur = durations_ps(
                args.nominal, args.data, s["sp"] / 100, s["dsp"] / 100, s["brs"]
            )
            frames.append(
                {
                    "id": s["id"],
                    "fb": fb,
                    "tags": tags,
                    "dur": dur,
                    "edge_ps": round(s["edge"] * 1e3),
                    "rise_ps": round(s["rise"] * 1e3),
                    "dominant_v": s["dominant"],
                }
            )
        values, period = waveform(frames, args.dip, args.dip_at)
    except ValueError as e:
        ap.error(str(e))

    full_v = max(f["dominant_v"] for f in frames)
    if not args.sender:
        print("".join(str(b) for b in fb))
        print("".join(t[0] for t in tags))
        print(
            f"{len(fb)} bits, BRS {dur['brs'] / 1e3:g} ns, "
            f"CRC delimiter {dur['crcdel'] / 1e3:g} ns, "
            f"{len(values)} points at {period / 1e3:g} ns"
        )
    else:
        for i, f in enumerate(frames, 1):
            print(f"sender {i}: ID 0x{f['id']:03X}")
            print("  " + "".join(str(b) for b in f["fb"]))
            print("  " + "".join(t[0] for t in f["tags"]))
        for i, f in enumerate(frames, 1):
            print(
                f"sender {i}: ID 0x{f['id']:03X}, {len(f['fb'])} bits, "
                f"BRS {f['dur']['brs'] / 1e3:g} ns, "
                f"CRC delimiter {f['dur']['crcdel'] / 1e3:g} ns, "
                f"dominant {f['dominant_v']:g} V, "
                f"edge {f['edge_ps'] / 1e3:g} ns, rise {f['rise_ps'] / 1e3:g} ns"
            )
        print(
            f"{len(values)} points at {period / 1e3:g} ns, "
            f"{len(values) * period / 1e6:g} us, "
            f"{1e12 / (len(values) * period):.2f} Hz, full scale {full_v:g} V"
        )

    if args.scope:
        sign = 1 if args.polarity == "H-L" else -1
        if sign < 0:
            values = [-v for v in values]
        low, high = sorted((0.0, sign * full_v))
        frequency = 1e12 / (len(values) * period)
        with KeysightMSO(pyvisa.ResourceManager("@py"), args.scope) as scope:
            scope.wgen_arb(values, frequency, low, high)
        print(f"playing on {args.scope} at {frequency:.2f} Hz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
