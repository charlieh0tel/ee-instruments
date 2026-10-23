#!/usr/bin/env python

"""Generate CAN FD eye-diagram mask files for Keysight InfiniiVision 3000T/4000X.

Reproduces the structure of Keysight's published CAN-FD(L-H|H-L) 500k-xMbps.msk
files (app note 5992-0437EN, "CAN FD Eye-Diagram Mask Testing",
https://www.keysight.com/us/en/assets/7018-04704/application-notes/5992-0437.pdf)
for arbitrary nominal / data-phase bit rates.

Only three things in those files depend on bit rate:
  :MTES:SCAL:XDEL        horizontal span of the mask = 10 data-phase bits
  :MTES:TSEQ:CAN:FD      data-phase rate for the scope's CAN FD test sequencer
  :TRIGger:GLITch:RANGe  pulse-width window catching the recessive BRS bit.
                         The rate switches at BRS's sample point, so it lasts
                         sp * t_nominal + (1 - dsp) * t_data. Keysight's
                         1.50-1.75 us at 500k = 75-87.5 % sp, ignoring the
                         data-phase tail (25 ns at 10M, but 125 ns at 2M).

Mask geometry (30-80 % of each bit, 0.5-0.9 V thresholds) is rate independent
and copied unchanged.

Keysight only publishes masks for 500k / 4-10M, but firmware 07.60 on an
MSO-X 4054A accepts other sequencer rates (2M ok; nonsense values get -222).

--upload also loads the mask onto a scope over VISA: mask test off (exiting
any running eye test), Default Setup (*RST; --no-reset to skip), every channel
but --channel off, then the mask. The TSEQ line is held back and sent on its
own, only with --start: sent inside the mask upload it wedged the scope until
a power cycle.

Examples:
  canfd_mask_gen.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75
  canfd_mask_gen.py 500k 5M --polarity L-H -o masks/
  canfd_mask_gen.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 --polarity L-H \\
      --upload TCPIP::a-mx4054a-10545.local::5025::SOCKET --start
  canfd_mask_gen.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 --polarity H-L \\
      --upload TCPIP::a-mx4054a-10545.local::5025::SOCKET --collect   # ^C ends
  canfd_mask_gen.py --verify .      # regenerate Keysight's files and compare
"""

import argparse
import csv
import math
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import pyvisa

from keysight_mso import KeysightMSO

NUM_BITS = 10  # sequencer tests the first 10 data-phase bits

# Polarity-dependent setup values, copied from Keysight's files.
POLARITY = {
    # dominant-bit low: + lead on CAN_L
    "L-H": {
        "y1": "-9.0E-01",
        "y2": "-5.0E-01",
        "offset": "-1.6E-0",
        "glitch_pol": "POSitive",
        "glitch_level": "-0.7",
    },
    # dominant-bit high: + lead on CAN_H
    "H-L": {
        "y1": "5.0E-01",
        "y2": "9.0E-01",
        "offset": "600E-03",
        "glitch_pol": "NEGative",
        "glitch_level": "0.7",
    },
}


def parse_rate(text):
    """'500k', '1M', '2.5M', '2000000' -> bits/s."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*([kKmM]?)(?:bps)?\s*", text)
    if not m:
        raise argparse.ArgumentTypeError(f"bad bit rate: {text!r}")
    mult = {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()]
    return round(float(m.group(1)) * mult)


def rate_label(bps):
    """2000000 -> '2M', 500000 -> '500k', 2500000 -> '2.5M'."""
    if bps >= 1_000_000:
        return f"{bps / 1e6:g}M"
    return f"{bps / 1e3:g}k"


def region_lines(pair):
    """One region covers two consecutive bits: two hexagons joined by a strip."""
    a = pair * 0.2  # start of first bit (normalized, 0.1 per bit)
    b = a + 0.1  # start of second bit
    pts = [
        (a + 0.030, ".500"),
        (a + 0.035, "1.000"),
        (a + 0.075, "1.000"),
        (a + 0.080, ".500"),
        (a + 0.080, "MIN"),
        (b + 0.030, "MIN"),
        (b + 0.030, ".500"),
        (b + 0.035, "1.000"),
        (b + 0.075, "1.000"),
        (b + 0.080, ".500"),
        (b + 0.075, "0.000"),
        (b + 0.035, "0.000"),
        (b + 0.030, ".500"),
        (a + 0.080, ".500"),
        (a + 0.075, "0.000"),
        (a + 0.035, "0.000"),
    ]
    lines = [
        f"/* Bit{2 * pair + 1},{2 * pair + 2} */",
        f"/* Region Number         */  {pair + 1}",
        f"/* Number of vertices    */  {len(pts)}",
    ]
    for x, y in pts:
        y = "0" + y if y.startswith(".") else y
        lines.append(f"                {x:.4f},       {y}")
    lines.append("")
    return lines


def us(seconds):
    """Seconds -> 'x.xx[x]E-06' in microseconds, at least two decimals."""
    s = f"{seconds * 1e6:.4f}".rstrip("0")
    whole, frac = s.split(".")
    return f"{whole}.{frac.ljust(2, '0')}E-06"


def brs_window(nominal, data, sp_min, sp_max, dsp):
    """(shortest, longest) recessive BRS pulse, in seconds."""
    tail = (1 - dsp) / data
    return sp_min / nominal + tail, sp_max / nominal + tail


def crc_delimiter(nominal, data, sp_min, dsp):
    """Longest recessive CRC delimiter (the rate switches back at its sp)."""
    return dsp / data + (1 - sp_min) / nominal


def build_mask(nominal, data, polarity, sp_min, sp_max, dsp):
    p = POLARITY[polarity]
    brs_min, brs_max = brs_window(nominal, data, sp_min, sp_max, dsp)
    xdel = NUM_BITS / data
    title = f"CAN-FD({polarity}) {rate_label(nominal)}-{rate_label(data)}bps"

    lines = ["MASK_FILE_548XX", "", f'"{title}"', ""]
    for pair in range(NUM_BITS // 2):
        lines += region_lines(pair)
    lines += [
        "setup",
        ":MTES:ENAB 1",
        ":MTES:LOCK 1",
        ":MTES:SCAL:BIND OFF",
        ":MTES:SCAL:X1 0.0E-0",
        f":MTES:SCAL:XDEL {xdel * 1e6:.4g}E-6",
        f":MTES:SCAL:Y1 {p['y1']}",
        f":MTES:SCAL:Y2 {p['y2']}",
        ":MTES:RMOD Forever",
        ":MTES:RMOD:FACT:STOP OFF",
        ":CHAN%d:SCAL 600E-03",
        f":CHAN%d:OFFS {p['offset']}",
        ":CHAN%d:BWL OFF",
        ":TIMebase:MODE MAIN",
        ":TIMebase:REFerence CENT",
        ":TRIGger:Sweep NORMal",
        ":TRIGger:MODE GLITch",
        ":TRIGger:GLITch:QUALifier RANGe",
        f":TRIGger:GLITch:RANGe {us(brs_max)},{us(brs_min)}",
        ":TRIGger:GLITch:SOURce CHAN%d",
        f":TRIGger:GLITch:POLarity {p['glitch_pol']}",
        f":TRIGger:GLITch:LEVel {p['glitch_level']},CHAN%d",
        ":TRIGger:NREJect ON",
        ":DISP:INT:WAV 75",
        ":ACQ:MODE RTIM",
        ":ACQ:TYPE NORM",
        ":SYSTem:MENU MASK",
        ":RUN",
        f":MTES:TSEQ:CAN:FD {data}",
        "end_setup",
        "",
    ]
    filename = f"CAN-FD({polarity}) {rate_label(nominal)}-{rate_label(data)}bps.msk"
    return filename, "\r\n".join(lines)  # scope files use CRLF


def tokens(text):
    """Comparable token list: comments and title dropped, numbers as floats."""
    text = re.sub(r"/\*.*?\*/", " ", text)
    text = re.sub(r'"[^"]*"', " ", text)
    out = []
    for tok in re.split(r"[\s,]+", text):
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            out.append(tok)
    return out


def same(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if isinstance(x, float) and isinstance(y, float):
            if not math.isclose(x, y, rel_tol=1e-3, abs_tol=1e-9):
                return False
        elif x != y:
            return False
    return True


def verify(directory):
    """Regenerate every Keysight 500k-xMbps file found and compare numerically."""
    files = sorted(Path(directory).glob("CAN-FD(*) 500k-*Mbps.msk"))
    if not files:
        print(f"no Keysight mask files found in {directory}", file=sys.stderr)
        return 1
    failed = 0
    for f in files:
        m = re.fullmatch(r"CAN-FD\((L-H|H-L)\) 500k-(\d+)Mbps\.msk", f.name)
        if not m:
            continue
        # Keysight's windows leave out BRS's data-phase tail, i.e. dsp = 100 %.
        _, text = build_mask(
            500_000, int(m.group(2)) * 1_000_000, m.group(1), 0.75, 0.875, 1.0
        )
        ok = same(tokens(f.read_text()), tokens(text))
        failed += not ok
        print(f"{'OK  ' if ok else 'DIFF'}  {f.name}")
    return 1 if failed else 0


def poll(seconds, interval, sample):
    """Call sample(wall_s) -> (csv_row, status_line) every interval for seconds
    (math.inf: until Ctrl-C), showing the line; returns the rows."""
    stop = False

    def on_sigint(signum, frame):
        nonlocal stop
        stop = True

    tty = sys.stdout.isatty()
    rows = []
    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        start = time.monotonic()
        while not stop:
            remaining = seconds - (time.monotonic() - start)
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
            row, line = sample(time.monotonic() - start)
            rows.append(row)
            print("\r" + line if tty else line, end="" if tty else "\n", flush=True)
    finally:
        signal.signal(signal.SIGINT, previous)
    if tty:
        print()
    return rows


def run_stem(outdir, base):
    """outdir/<base>-YYMMDD-HHMMSS. Not checked for existence (that would
    race); save_run() creates files exclusively instead."""
    return outdir / f"{base}-{datetime.now().astimezone():%y%m%d-%H%M%S}"


def save_run(stem, header, rows, png):
    """Create <stem>.csv and <stem>.png, failing rather than overwriting an
    existing file. Not with_suffix(): rate labels like 2.5Mbps contain dots."""
    csv_path = stem.parent / f"{stem.name}.csv"
    with open(csv_path, "x", newline="") as f:
        out = csv.writer(f)
        out.writerow(header)
        out.writerows(rows)
    print(csv_path)
    png_path = stem.parent / f"{stem.name}.png"
    with open(png_path, "xb") as f:
        f.write(png)
    print(png_path)


def collect(scope, seconds, stem, interval):
    """Print eye-test stats for seconds (math.inf: until Ctrl-C), then save the
    stats history as <stem>.csv and a screenshot as <stem>.png.

    The scope's own test time runs slower than the wall clock (it seems to
    count only time spent testing), so both are recorded.
    """

    def sample(wall_s):
        tested, failed, test_s = scope.mask_counts()
        rate = failed / tested if tested else 0.0
        row = (f"{wall_s:.1f}", f"{test_s:.1f}", tested, failed, f"{rate:.6f}")
        line = f"{wall_s:7.1f} s  {tested:10d} tested  {failed:8d} failed  "
        return row, line + f"{100 * rate:8.4f} %"

    scope.reset_mask_counts()
    rows = poll(seconds, interval, sample)
    header = ["wall_s", "test_s", "tested", "failed", "failure_rate"]
    save_run(stem, header, rows, scope.get_png())
    if rows and rows[-1][2] == 0:
        print(
            "nothing tested: no BRS triggers (traffic, level, --sp-*/--dsp?)",
            file=sys.stderr,
        )


def upload(resource, text, channel, data, start, reset, collect_s, stem, interval):
    """Load the mask on the scope; start the eye test only if asked."""
    tseq = re.compile(r"^:MTES:TSEQ\S*\s.*\n?", re.MULTILINE)
    body = tseq.sub("", text.replace("\r\n", "\n"))
    with KeysightMSO(pyvisa.ResourceManager("@py"), resource) as scope:
        probe = scope.prepare_channel(channel, reset)
        scope.load_mask(body, channel)
        print(f"loaded mask on {resource} CHAN{channel}")
        print("probe {}, {:g}:1, {}".format(*probe))
        scope.check_probe(channel, probe)
        if start:
            scope.start_canfd_eye(data)
            print(f"started CAN FD eye test at {rate_label(data)}bps")
            if collect_s is not None:
                collect(scope, collect_s, stem, interval)
                print("eye test left running")
        else:
            print("eye test not started (pass --start once traffic is present)")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "nominal",
        nargs="?",
        type=parse_rate,
        help="nominal (arbitration) bit rate, e.g. 500k, 1M",
    )
    ap.add_argument(
        "data", nargs="?", type=parse_rate, help="data-phase bit rate, e.g. 2M, 5M"
    )
    ap.add_argument(
        "--polarity",
        choices=["L-H", "H-L", "both"],
        default="both",
        help="L-H = dominant low, H-L = dominant high (default both)",
    )
    ap.add_argument(
        "--sp-min",
        type=float,
        default=75.0,
        help="lowest nominal-phase sample point, %% (default 75)",
    )
    ap.add_argument(
        "--sp-max",
        type=float,
        default=87.5,
        help="highest nominal-phase sample point, %% (default 87.5)",
    )
    ap.add_argument(
        "--dsp",
        type=float,
        default=75.0,
        help="data-phase sample point, %% (default 75)",
    )
    ap.add_argument("-o", "--outdir", default=".", help="output directory")
    ap.add_argument(
        "--upload",
        metavar="RESOURCE",
        help="also load the mask onto a scope (VISA resource)",
    )
    ap.add_argument(
        "--channel", type=int, default=1, help="scope input with the probe (default 1)"
    )
    ap.add_argument(
        "--no-reset",
        dest="reset",
        action="store_false",
        help="with --upload, skip the Default Setup (*RST) done before loading",
    )
    ap.add_argument(
        "--start",
        action="store_true",
        help="with --upload, also start the eye test; needs CAN FD traffic present",
    )
    ap.add_argument(
        "--collect",
        nargs="?",
        type=float,
        const=math.inf,
        metavar="SECONDS",
        help="with --upload: start the eye test, print stats for SECONDS (no "
        "value: until Ctrl-C), then save stats CSV and screenshot PNG",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="seconds between --collect stats readings (default 1)",
    )
    ap.add_argument(
        "--verify",
        metavar="DIR",
        help="check generator against Keysight's 500k files in DIR",
    )
    args = ap.parse_args()

    if args.verify:
        return verify(args.verify)
    if args.nominal is None or args.data is None:
        ap.error("nominal and data bit rates are required")
    if not 0 < args.sp_min < args.sp_max < 100:
        ap.error("need 0 < --sp-min < --sp-max < 100")
    if not 0 < args.dsp < 100:
        ap.error("need 0 < --dsp < 100")
    if args.data <= args.nominal:
        ap.error("data-phase rate must be higher than nominal rate")
    sp_min, sp_max, dsp = args.sp_min / 100, args.sp_max / 100, args.dsp / 100
    brs_min, brs_max = brs_window(args.nominal, args.data, sp_min, sp_max, dsp)
    if brs_max >= 1 / args.nominal:
        ap.error(
            f"BRS window reaches {brs_max * 1e9:.0f} ns, a whole nominal bit: it "
            "would trigger on any lone recessive bit. Lower --sp-max."
        )
    crc_del = crc_delimiter(args.nominal, args.data, sp_min, dsp)
    if brs_min <= crc_del:
        ap.error(
            f"BRS window starts at {brs_min * 1e9:.0f} ns, at or below the "
            f"{crc_del * 1e9:.0f} ns CRC delimiter. Raise --sp-min."
        )
    if args.upload and args.polarity == "both":
        ap.error("--upload needs --polarity L-H or H-L")
    if args.collect is not None:
        if not args.upload:
            ap.error("--collect needs --upload")
        if args.collect <= 0 or args.interval <= 0:
            ap.error("--collect and --interval must be positive")
        args.start = True
    if args.start and not args.upload:
        ap.error("--start needs --upload")
    if not 1 <= args.channel <= 4:
        ap.error("--channel must be 1-4")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pols = ["L-H", "H-L"] if args.polarity == "both" else [args.polarity]
    for pol in pols:
        name, text = build_mask(args.nominal, args.data, pol, sp_min, sp_max, dsp)
        (outdir / name).write_bytes(text.encode("ascii"))
        print(outdir / name)
    print(f"BRS trigger window {brs_min * 1e9:.1f}-{brs_max * 1e9:.1f} ns")
    if args.upload:
        upload(
            args.upload,
            text,
            args.channel,
            args.data,
            args.start,
            args.reset,
            args.collect,
            run_stem(outdir, Path(name).stem),
            args.interval,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
