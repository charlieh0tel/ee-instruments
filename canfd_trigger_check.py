#!/usr/bin/env python

"""Check that the CAN FD eye test's trigger catches the BRS bit, and only it.

segments: capture N segments in segmented memory with the mask test off,
save them, and classify each one offline.
  --trigger glitch  the eye test's BRS window (as canfd_eye_test sets it):
                    is every trigger a real BRS?
  --trigger sof     CAN start of frame, any frame: the BRS width of every
                    frame, whether or not it falls in the window.
analyze: re-run the classification on a saved .npz.
suggest: from sof captures, propose a BRS window (--window LO-HI) that clears
         the other recessive pulses, optionally at another --level.
awg: wavegen cases with the eye test running (see the trigger test plan).

Over USB the scope wedges until power-cycled if a query times out or a
running eye test is turned off over SCPI. segments refuses to start while the
mask test is on, waits for the capture by polling :OPERegister:CONDition?,
and fetches waveform data only once the capture is complete.

  canfd_trigger_check.py segments 1M 2M --trigger glitch --scope $R -o runs
  canfd_trigger_check.py analyze runs/trig-glitch-....npz
  canfd_trigger_check.py suggest runs/trig-sof-....npz --level 1.2
"""

import argparse
import csv
import math
import sys
import time
from pathlib import Path

import numpy as np
import pyvisa

from canfd_eye_test import IMPEDANCES, brs_window, parse_rate, parse_window
from keysight_mso import KeysightMSO

THRESHOLD = 0.7  # differential volts, as the glitch trigger and decoder use
# (default; --level changes it, and a capture's meta records the one used)
HYSTERESIS = 0.05  # volts each side of the threshold, for pulses()
RUN_BIT = 8  # :OPERegister:CONDition? bit 3, set while acquiring


def us(seconds):
    return f"{seconds * 1e6:.4f}E-06"


def setup_capture(scope, args, sign, window):
    """Channel, timebase, trigger and segmented acquisition. No *RST.
    window: (shortest, longest) BRS pulse in s, for the glitch trigger."""
    w = scope.inst.write
    ch = f"CHAN{args.channel}"
    w(":STOP")
    probe = scope.prepare_channel(
        args.channel,
        reset=False,
        impedance=IMPEDANCES[args.impedance],
        attenuation=args.probe,
    )
    w(f":{ch}:SCALe 0.6")
    w(f":{ch}:OFFSet {sign * 0.6}")
    w(f":{ch}:BWLimit OFF")
    if args.trigger == "glitch":
        brs_min, brs_max = window
        w(":LISTer:DISPlay OFF")  # left on by a sof capture
        w(":SBUS1:DISPlay 0")
        w(":TRIGger:MODE GLITch")
        w(":TRIGger:GLITch:QUALifier RANGe")
        w(f":TRIGger:GLITch:RANGe {us(brs_max)},{us(brs_min)}")
        w(f":TRIGger:GLITch:SOURce {ch}")
        w(f":TRIGger:GLITch:POLarity {'POSitive' if sign < 0 else 'NEGative'}")
        w(f":TRIGger:GLITch:LEVel {sign * args.level:g},{ch}")
        w(":TRIGger:NREJect ON")
        t0, t1 = -34e-6, 6e-6  # back to the idle before SOF, extended IDs too
    else:
        scope.setup_canfd_decode(
            args.channel,
            args.nominal,
            args.data,
            args.sp,
            args.dsp,
            sign * args.level,
            "SOF",
        )
        w(":STOP")
        # Decoding every segment into the lister kept the scope busy for
        # over 30 s after the capture; the trigger doesn't need it.
        w(":LISTer:DISPlay OFF")
        t0, t1 = -12e-6, 40e-6  # the idle before SOF, through ESI
    w(":TRIGger:SWEep NORMal")
    if args.trigger == "glitch":  # with the CAN lister showing: settings conflict
        w(":TIMebase:MODE MAIN")
    w(":TIMebase:REFerence CENTer")
    w(f":TIMebase:RANGe {t1 - t0:.6e}")
    w(f":TIMebase:POSition {(t0 + t1) / 2:.6e}")
    w(":ACQuire:TYPE NORMal")
    w(":ACQuire:MODE SEGMented")
    w(f":ACQuire:SEGMented:COUNt {args.segments}")
    if errs := scope.errors():
        raise RuntimeError(f"capture setup failed: {errs}")
    scope.check_probe(args.channel, probe)


def acquire(scope, nseg, wait_s):
    """:SINGle and wait until it stops, polling short status queries only.

    Returns the number of segments captured: 0 if nothing triggered within
    wait_s (the scope is then stopped and nothing is fetched).
    """
    q = scope.inst.query
    q(":TER?")  # clear the trigger event latch
    scope.inst.write(":SINGle")
    deadline = time.monotonic() + wait_s
    triggered = False
    while time.monotonic() < deadline:
        time.sleep(0.25)
        triggered |= bool(int(q(":TER?")))
        if triggered and not int(q(":OPERegister:CONDition?")) & RUN_BIT:
            break
    else:
        scope.inst.write(":STOP")
        time.sleep(0.5)
        return 0
    n = int(float(q(":WAVeform:SEGMented:COUNt?")))
    if n != nseg:
        raise RuntimeError(f"asked for {nseg} segments, scope has {n}")
    return n


def fetch(scope, channel, nseg):
    """(times, volts per segment, time tags) for the completed acquisition."""
    q, w = scope.inst.query, scope.inst.write
    w(f":WAVeform:SOURce CHAN{channel}")
    w(":WAVeform:FORMat WORD")
    w(":WAVeform:BYTeorder LSBFirst")
    w(":WAVeform:UNSigned 0")
    w(":WAVeform:POINts:MODE RAW")
    w(":WAVeform:POINts MAXimum")
    segs, tags, pre = [], [], None
    for i in range(1, nseg + 1):
        w(f":ACQuire:SEGMented:INDex {i}")
        p = [float(x) for x in q(":WAVeform:PREamble?").split(",")]
        if pre is None:
            pre = p
        elif p[2] != pre[2] or p[4:7] != pre[4:7]:
            raise RuntimeError(f"segment {i} preamble differs: {p} vs {pre}")
        tags.append(float(q(":WAVeform:SEGMented:TTAG?")))
        raw = scope.inst.query_binary_values(
            ":WAVeform:DATA?", datatype="h", container=np.array
        )
        if len(raw) != int(p[2]):
            raise RuntimeError(f"segment {i}: {len(raw)} points, preamble {p[2]}")
        yinc, yorg, yref = p[7], p[8], p[9]
        segs.append((raw - yref) * yinc + yorg)
    npts, xinc, xorg, xref = int(pre[2]), pre[4], pre[5], pre[6]
    t = (np.arange(npts) - xref) * xinc + xorg
    return t, np.array(segs), np.array(tags)


def pulses(t, v, sign, threshold=THRESHOLD, hysteresis=HYSTERESIS):
    """[(level, start, end)] at the threshold (differential volts, magnitude;
    sign gives the dominant direction); level 1 dominant, 0 recessive.

    The level changes only once the signal is hysteresis volts past the
    threshold, so noise on a slow edge doesn't split it into runt pulses; the
    edge time is still where it last crossed the threshold itself,
    interpolated. The first and last runs are open-ended (start or end at the
    record's edge).
    """
    s = sign * v
    # Schmitt state: 1 above threshold + h, 0 below threshold - h, else held.
    mark = np.full(len(s), -1)
    mark[s > threshold + hysteresis] = 1
    mark[s < threshold - hysteresis] = 0
    known = np.flatnonzero(mark >= 0)
    if len(known) == 0:
        return [(int(s[0] > threshold), t[0], t[-1])]
    state = mark[
        known[np.maximum(np.searchsorted(known, np.arange(len(s)), "right") - 1, 0)]
    ]
    flips = np.flatnonzero(state[1:] != state[:-1]) + 1
    cross = np.flatnonzero((s[1:] > threshold) != (s[:-1] > threshold))
    edges = []
    for f in flips:
        k = np.searchsorted(cross, f, "right") - 1  # last crossing before f
        i = cross[k] if k >= 0 else f - 1
        v0, v1 = s[i] - threshold, s[i + 1] - threshold
        edges.append(t[i] + (t[i + 1] - t[i]) * v0 / (v0 - v1) if v0 != v1 else t[i])
    out, start = [], t[0]
    level = int(state[0])
    for e in edges:
        out.append((level, start, e))
        level, start = 1 - level, e
    out.append((level, start, t[-1]))
    return out


def decode_header(ps, nominal, data, sp, dsp, t_from=None, t_to=None):
    """Read a frame from its SOF: a dominant edge after at least 11 recessive
    bits. With t_from, the first such edge from t_from on; with t_to, the last
    one before t_to. Samples at the nominal sample
    point, hard-syncing on every recessive-to-dominant edge, and checks each
    stuff bit. Returns a dict, or None if no SOF or a stuff error.

    Keys: id (11 or 29 bits), ide, fdf, brs, esi (sampled at the data-phase
    sample point; None if classical or no BRS), brs_width (s, the recessive
    pulse holding the BRS sample; None if ESI is recessive too, so the pulse
    is BRS+ESI), brs_end (s).
    """
    tb, td = 1 / nominal, 1 / data
    sofs = [
        b
        for a, b in zip(ps, ps[1:], strict=False)
        if a[0] == 0
        and b[0] == 1
        and a[2] - a[1] >= 11 * tb * 0.95
        and (t_from is None or b[1] >= t_from)
        and (t_to is None or b[1] < t_to)
    ]
    if not sofs:
        return None
    sof = sofs[-1] if t_to is not None else sofs[0]
    if sof is None:
        return None
    falls = [p[1] for p in ps if p[0] == 1 and p[1] >= sof[1]]

    def level_at(x):
        for p in ps:
            if p[1] <= x < p[2]:
                return p[0]
        return None

    bits, sync, n, run_bit, run = [], sof[1], 0, None, 0
    sample_t = []

    def need():
        if len(bits) >= 14 and bits[13] == 1:  # IDE: extended
            return 1 + 11 + 1 + 1 + 18 + 1 + 1 + 1 + 1  # ..SRR IDE id RRS FDF res BRS
        return 1 + 11 + 1 + 1 + 1 + 1 + 1  # SOF id RRS IDE FDF res BRS

    while len(bits) < need():
        x = sync + (n + sp) * tb
        lvl = level_at(x)
        if lvl is None:
            return None
        b = 1 - lvl  # 1 = recessive
        n += 1
        nxt = [f for f in falls if sync + (n - 0.5) * tb < f < sync + (n + 0.5) * tb]
        if nxt:
            sync, n = nxt[0], 0
        if run == 5:  # this must be a stuff bit of the opposite level
            if b == run_bit:
                return None
            run_bit, run = b, 1
            continue
        bits.append(b)
        sample_t.append(x)
        run = run + 1 if b == run_bit else 1
        run_bit = b
        if len(bits) >= 15 and need() == 17 and bits[14] == 0:
            break  # classical frame: FDF dominant
        if need() > 17 and len(bits) >= 34 and bits[33] == 0:
            break  # classical extended frame
    ext = len(bits) > 13 and bits[13] == 1
    if ext:
        can_id = int("".join(map(str, bits[1:12] + bits[14:32])), 2)
        fdf_i = 33
    else:
        can_id = int("".join(map(str, bits[1:12])), 2)
        fdf_i = 14
    out = {"id": can_id, "ide": int(ext), "fdf": bits[fdf_i]}
    out.update(brs=None, esi=None, brs_width=None, brs_end=None)
    if not out["fdf"] or len(bits) < fdf_i + 3:
        return out
    out["brs"] = bits[fdf_i + 2]
    if not out["brs"]:
        return out
    t_sp = sample_t[fdf_i + 2]  # BRS sample point: the rate switches here
    esi_lvl = level_at(t_sp + (1 - dsp) * td + dsp * td)
    if esi_lvl is None:
        return out
    out["esi"] = 1 - esi_lvl
    brs = next((p for p in ps if p[0] == 0 and p[1] <= t_sp < p[2]), None)
    if brs is not None and not out["esi"]:
        out["brs_width"] = brs[2] - brs[1]
        out["brs_end"] = brs[2]
    return out


def classify_glitch(ps, hdr, window, t_end):
    """A glitch-triggered segment is a BRS trigger when the frame decodes as
    CAN FD with BRS, its BRS pulse ends at the trigger (t = 0, within 100 ns)
    with a width near the window, and data-rate bits follow: some pulse of
    400-600 ns in the 5 us after it. Returns (verdict, trigger width, why);
    verdict is "brs", "wrong" or "ambiguous" (frame start not captured, but
    the bits around the trigger look like a BRS)."""
    lo, hi = window
    trig = min(
        (p for p in ps if p[0] == 0 and p[1] > ps[0][1] and p[2] < t_end),
        key=lambda p: abs(p[2]),
        default=None,
    )
    if trig is None or abs(trig[2]) > 100e-9:
        return "wrong", None, "no recessive pulse ending at the trigger"
    width = trig[2] - trig[1]
    after = [p[2] - p[1] for p in ps if 0 <= p[1] < 5e-6 and p[2] < t_end]
    data_bits = any(400e-9 < w < 600e-9 for w in after)
    why = f"width {width * 1e9:.0f} ns, data-rate bits {data_bits}"
    if not lo - 50e-9 < width < hi + 50e-9:
        return "wrong", width, why + ", width far outside window"
    if hdr is None:
        # No SOF in the record: judge by the bits around the trigger. A BRS
        # follows FDF (recessive) and res (dominant, one nominal bit).
        k = ps.index(trig)
        res_w = ps[k - 1][2] - ps[k - 1][1] if k >= 2 else 0
        fdf_w = ps[k - 2][2] - ps[k - 2][1] if k >= 2 else 0
        why += f", res {res_w * 1e9:.0f} ns, FDF {fdf_w * 1e9:.0f} ns"
        if 0.8e-6 < res_w < 1.2e-6 and fdf_w > 0.8e-6 and data_bits:
            return "ambiguous", width, why + ", frame start not decoded"
        return "wrong", width, why + ", frame start not decoded"
    why += f", fdf {hdr['fdf']} brs {hdr['brs']}"
    is_brs = (
        hdr["fdf"] == 1
        and hdr["brs"] == 1
        and hdr["brs_end"] is not None
        and abs(hdr["brs_end"] - trig[2]) < 20e-9
        and data_bits
    )
    return ("brs" if is_brs else "wrong"), width, why


def analyze(path):
    d = np.load(path, allow_pickle=True)
    meta = d["meta"].item()
    t, segs, tags = d["t"], d["segs"], d["tags"]
    sign, window = meta["sign"], meta["window"]
    level = meta.get("level", THRESHOLD)  # older captures: always 0.7
    lo, hi = window
    timing = (meta["nominal"], meta["data"], meta["sp"] / 100, meta["dsp"] / 100)
    rows = []
    for i, v in enumerate(segs):
        ps = pulses(t, v, sign, level)
        if meta["trigger"] == "glitch":
            hdr = decode_header(ps, *timing, t_to=0)
            verdict, width, why = classify_glitch(ps, hdr, window, t[-1])
        else:
            hdr = decode_header(ps, *timing, t_from=-2e-6)
            verdict, width, why = None, None, ""
        h = hdr or {}
        brs_w = h.get("brs_width")
        rows.append(
            {
                "segment": i + 1,
                "ttag_s": tags[i],
                "verdict": verdict,
                "trigger_width_ns": None if width is None else round(width * 1e9, 1),
                "id": None if not hdr else f"0x{h['id']:X}",
                "ide": h.get("ide"),
                "fdf": h.get("fdf"),
                "brs": h.get("brs"),
                "esi": h.get("esi"),
                "brs_width_ns": None if brs_w is None else round(brs_w * 1e9, 1),
                "why": why,
            }
        )
    out = Path(path).with_suffix(".csv")
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    print(out)
    dt = t[1] - t[0]
    print(
        f"{len(segs)} segments, {len(t)} points each, {dt * 1e9:.2f} ns apart, "
        f"{t[0] * 1e6:.1f} to {t[-1] * 1e6:.1f} us"
    )
    span = tags.max() - tags.min() if len(tags) > 1 else 0
    if span > 0:
        print(f"segments over {span:.3f} s: {(len(segs) - 1) / span:.0f}/s")
    print(f"window {lo * 1e9:.1f}-{hi * 1e9:.1f} ns, level {level:g} V")
    n = len(rows)
    if meta["trigger"] == "glitch":
        counts = {
            k: sum(r["verdict"] == k for r in rows)
            for k in ("brs", "wrong", "ambiguous")
        }
        print(", ".join(f"{k} {v}" for k, v in counts.items()))
        if counts["wrong"] == 0 and counts["brs"]:
            print(
                f"0 wrong in {counts['brs']}: 95 % upper bound {3 / counts['brs']:.2%}"
            )
        for r in [r for r in rows if r["verdict"] != "brs"][:10]:
            print(f"  segment {r['segment']} {r['verdict']}: {r['why']}")
        key = "trigger_width_ns"
        groups = [r for r in rows if r["verdict"] == "brs"]
    else:
        unread = sum(r["id"] is None for r in rows)
        classic = sum(r["fdf"] == 0 for r in rows)
        no_brs = sum(r["fdf"] == 1 and r["brs"] == 0 for r in rows)
        esi = sum(r["esi"] == 1 for r in rows)
        print(
            f"unreadable {unread}, classical {classic}, FD without BRS {no_brs}, "
            f"ESI recessive (error passive) {esi}, of {n}"
        )
        key = "brs_width_ns"
        groups = rows
    by_id = {}
    for r in groups:
        if r.get(key) is not None:
            by_id.setdefault(r["id"], []).append(r[key])
    print(f"{'id':>10} {'n':>5} {'min':>7} {'median':>7} {'max':>7}  in window")
    for k in sorted(by_id, key=str):
        w = np.array(by_id[k])
        inside = np.mean((w > lo * 1e9) & (w < hi * 1e9))
        print(
            f"{k!s:>10} {len(w):5d} {w.min():7.1f} {np.median(w):7.1f} "
            f"{w.max():7.1f}  {inside:6.1%}"
        )
    return 0


def suggest(args):
    """Propose a BRS trigger window from SOF captures: the BRS widths of
    error-active frames, and the nearest other recessive pulses on each side
    (CRC delimiter below; lone nominal bits, FDF, 2-bit data runs above)."""
    timing, by_id, others = None, {}, []
    skipped = no_brs = passive = 0
    for path in args.npz:
        d = np.load(path, allow_pickle=True)
        meta = d["meta"].item()
        if meta["trigger"] != "sof":
            print(f"{path}: not a --trigger sof capture", file=sys.stderr)
            return 2
        tm = (meta["nominal"], meta["data"], meta["sp"] / 100, meta["dsp"] / 100)
        if timing is None:
            timing = tm
        elif tm != timing:
            print(f"{path}: bit rates or sample points differ", file=sys.stderr)
            return 2
        level = args.level or meta.get("level", THRESHOLD)
        t = d["t"]
        for v in d["segs"]:
            ps = pulses(t, v, meta["sign"], level)
            hdr = decode_header(ps, *timing, t_from=-2e-6)
            if hdr is None or (hdr["brs"] and hdr["esi"] is None):
                skipped += 1  # can't tell which pulse is the BRS
                continue
            no_brs += bool(hdr["fdf"] and not hdr["brs"])
            passive += bool(hdr["esi"])
            if hdr["brs_width"] is not None:
                by_id.setdefault(hdr["id"], []).append(hdr["brs_width"])
            others += [
                p[2] - p[1]
                for p in ps
                if p[0] == 0
                and t[0] < p[1]
                and p[2] < t[-1]
                and (hdr["brs_end"] is None or abs(p[2] - hdr["brs_end"]) > 1e-12)
            ]
    nominal = timing[0]
    lvl = f"{args.level:g} V" if args.level else "as captured"
    print(
        f"level {lvl}; {skipped} segments not decoded, left out; "
        f"{no_brs} FD without BRS, {passive} error passive (BRS+ESI one pulse)"
    )
    if not by_id:
        print("no error-active CAN FD frames with BRS decoded")
        return 1
    print("BRS width, ns (error-active frames with BRS)")
    print(f"{'id':>10} {'n':>5} {'min':>7} {'median':>7} {'max':>7}")
    for k in sorted(by_id):
        w = np.array(by_id[k]) * 1e9
        print(
            f"{f'0x{k:X}':>10} {len(w):5d} {w.min():7.1f} {np.median(w):7.1f} "
            f"{w.max():7.1f}"
        )
    brs = np.concatenate([by_id[k] for k in by_id])
    b_lo, b_hi = brs.min(), brs.max()
    others = np.array(others)
    near = np.sort(others[(others > 600e-9) & (others < 1200e-9)]) * 1e9
    print(f"other recessive pulses 600-1200 ns: {len(near)}")
    for b in np.unique(np.floor(near / 25) * 25):
        n = np.sum((near >= b) & (near < b + 25))
        print(f"  {b:6.0f}-{b + 25:<6.0f} {n:5d}")
    inside = others[(others >= b_lo) & (others <= b_hi)]
    below = others[others < b_lo]
    above = others[others > b_hi]
    m = args.margin * 1e-9
    lo, hi = b_lo - m, b_hi + m
    ok = True
    if len(inside):
        print(
            f"{len(inside)} other pulses as wide as BRS pulses "
            f"({inside.min() * 1e9:.1f}-{inside.max() * 1e9:.1f} ns)"
        )
        ok = False
    # The CRC delimiter (about dsp data bits + 1 - sp nominal) is often past
    # the record's end: estimate it, shrunk as much as the BRS pulses are.
    tn, td = 1 / nominal, 1 / timing[1]
    sp, dsp = timing[2], timing[3]
    shrink = sp * tn + (1 - dsp) * td - np.median(brs)
    crc_del = dsp * td + (1 - sp) * tn - shrink
    print(
        f"CRC delimiter, estimated from sp {sp:.0%} dsp {dsp:.0%} and the BRS "
        f"shrinking {shrink * 1e9:.0f} ns: {crc_del * 1e9:.0f} ns"
    )
    below = np.append(below, crc_del)
    gap = b_lo - below.max()
    print(
        f"longest shorter pulse {below.max() * 1e9:.1f} ns, "
        f"{gap * 1e9:.1f} ns below the shortest BRS"
    )
    ok &= gap >= 2 * m
    if len(above):
        gap = above.min() - b_hi
        print(
            f"shortest longer pulse {above.min() * 1e9:.1f} ns (nominal bits, "
            f"2-bit data runs, ...), {gap * 1e9:.1f} ns above the longest BRS"
        )
        ok &= gap >= 2 * m
    if hi >= 1 / nominal:
        print(f"window would reach {hi * 1e9:.0f} ns, a whole nominal bit")
        ok = False
    print(
        f"BRS {b_lo * 1e9:.1f}-{b_hi * 1e9:.1f} ns, margin {args.margin:g} ns: "
        f"window {lo * 1e9:.0f}-{hi * 1e9:.0f} ns"
    )
    if not ok:
        print(f"no safe window at this level (needs {2 * args.margin:g} ns gaps)")
        return 1
    opt = f"--window {math.floor(lo * 1e9)}-{math.ceil(hi * 1e9)}"
    if args.level:
        opt += f" --level {args.level:g}"
    print(opt)
    return 0


def segments(args):
    sign = -1 if args.polarity == "L-H" else 1
    window = args.window or brs_window(
        args.nominal,
        args.data,
        args.sp_min / 100,
        args.sp_max / 100,
        args.dsp / 100,
    )
    with KeysightMSO(pyvisa.ResourceManager("@py"), args.scope) as scope:
        if int(scope.inst.query(":MTESt:ENABle?")):
            print(
                "mask test is on: turn it off on the front panel "
                "(Analyze > Features: Mask) and run again",
                file=sys.stderr,
            )
            return 1
        try:
            setup_capture(scope, args, sign, window)
            n = acquire(scope, args.segments, args.wait)
            if not n:
                print(f"no capture in {args.wait:g} s: nothing triggered")
                return 2
            t, segs, tags = fetch(scope, args.channel, n)
        finally:
            scope.inst.write(":ACQuire:MODE RTIMe")
    stamp = time.strftime("%y%m%d-%H%M%S")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"trig-{args.trigger}-{stamp}.npz"
    meta = {
        "trigger": args.trigger,
        "sign": sign,
        "window": window,
        "level": args.level,
        "nominal": args.nominal,
        "data": args.data,
        "sp": args.sp,
        "dsp": args.dsp,
        "resource": args.scope,
        "channel": args.channel,
        "note": args.note,
    }
    np.savez_compressed(path, t=t, segs=segs, tags=tags, meta=np.array(meta))
    print(path)
    return analyze(path)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sg = sub.add_parser("segments", help="capture and classify segments")
    sg.add_argument("nominal", type=parse_rate)
    sg.add_argument("data", type=parse_rate)
    sg.add_argument("--trigger", choices=["glitch", "sof"], default="glitch")
    sg.add_argument("--sp-min", type=float, default=70.0)
    sg.add_argument("--sp-max", type=float, default=80.0)
    sg.add_argument("--dsp", type=float, default=75.0)
    sg.add_argument("--sp", type=float, default=75.0, help="decoder nominal sp, %%")
    sg.add_argument(
        "--window",
        type=parse_window,
        metavar="LO-HI",
        help="glitch trigger BRS window, ns (default: from --sp-min/--sp-max/--dsp)",
    )
    sg.add_argument(
        "--level",
        type=float,
        default=THRESHOLD,
        metavar="V",
        help="trigger/decode level magnitude, V; sign follows --polarity "
        f"(default {THRESHOLD:g})",
    )
    sg.add_argument("--polarity", choices=["L-H", "H-L"], default="L-H")
    sg.add_argument("--channel", type=int, default=1)
    sg.add_argument("--impedance", choices=list(IMPEDANCES), default="50")
    sg.add_argument("--probe", type=float, default=10.0)
    sg.add_argument("--segments", type=int, default=500)
    sg.add_argument("--wait", type=float, default=20.0, help="capture timeout, s")
    sg.add_argument("--scope", required=True, metavar="RESOURCE")
    sg.add_argument("--note", default="", help="saved with the data")
    sg.add_argument("-o", "--outdir", default=".")
    an = sub.add_parser("analyze", help="classify a saved capture")
    an.add_argument("npz")
    su = sub.add_parser(
        "suggest", help="propose a BRS trigger window from SOF captures"
    )
    su.add_argument("npz", nargs="+", help="--trigger sof captures")
    su.add_argument(
        "--margin",
        type=float,
        default=20.0,
        help="ns kept between the window and the pulses on either side (default 20)",
    )
    su.add_argument(
        "--level",
        type=float,
        metavar="V",
        help="re-measure pulse widths at this level magnitude, V (default: the "
        "capture's, 0.7 for older files)",
    )
    args = ap.parse_args()
    if getattr(args, "level", None) is not None and args.level <= 0:
        ap.error("--level must be positive (its sign follows the polarity)")
    if args.cmd == "analyze":
        return analyze(args.npz)
    if args.cmd == "suggest":
        return suggest(args)
    return segments(args)


if __name__ == "__main__":
    sys.exit(main())
