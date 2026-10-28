#!/usr/bin/env python

"""Monitor CAN FD traffic on a Keysight InfiniiVision and count errors.

Sets up the scope's CAN FD decoder on the probed channel and prints its
running totals: frames, error frames (sent by nodes that saw a fault), spec
errors (malformed frames: CRC, stuff, form...), overload frames and bus
utilization. The scope counts these in hardware, so they cover all traffic,
not only what is captured. It triggers on errors (--trigger), so the screen
always holds the most recent bad frame.

Runs for --seconds or until Ctrl-C, then saves <stem>.csv (totals over time),
<stem>.png (screen) and <stem>-lister.csv (the decoder's frame list for that
acquisition), with stem "CAN-FD(H-L) 1M-2Mbps monitor-YYMMDD-HHMMSS".

Like canfd_eye_test --scope it first exits any eye test, does a Default Setup
(--no-reset to skip), turns off the other channels, and stops if the probe's
reported settings changed. See canfd_eye_testing.md.

Examples:
  canfd_monitor.py 1M 2M --sp 75 --dsp 75 --polarity L-H \\
      --scope TCPIP::a-mx4054a-10545.local::5025::SOCKET
  canfd_monitor.py 1M 2M --scope $R --seconds 600 --trigger CRCerror -o runs/
"""

import argparse
import math
import sys
from pathlib import Path

import pyvisa

from canfd_eye_test import IMPEDANCES, parse_rate, poll, rate_label, run_stem, save_run
from keysight_mso import KeysightMSO

# :SBUS1:CAN:TRIGger values for faults, as accepted by a 4054A (fw 07.60).
ERROR_TRIGGERS = [
    "ALLerrors",
    "ERRor",
    "SPECerror",
    "CRCerror",
    "STUFferror",
    "FORMerror",
    "ACKerror",
    "OVERload",
]
FIELDS = ["frames", "error_frames", "spec_errors", "overload", "utilization"]


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
        help="L-H = dominant low (+ lead on CAN_L), H-L = dominant high (default L-H)",
    )
    ap.add_argument(
        "--level",
        type=float,
        help="decode threshold, V (default 0.7 or -0.7: between the receiver "
        "thresholds)",
    )
    ap.add_argument("--scope", metavar="RESOURCE", required=True, help="VISA resource")
    ap.add_argument(
        "--channel", type=int, default=1, help="scope input with the probe (default 1)"
    )
    ap.add_argument(
        "--impedance",
        choices=IMPEDANCES,
        default="50",
        help="scope input impedance: 50 for the differential probe, 1M for the "
        "wavegen self-test (default 50)",
    )
    ap.add_argument(
        "--probe",
        type=float,
        default=10.0,
        metavar="RATIO",
        help="probe attenuation, N for N:1, for a probe the scope doesn't "
        "identify (default 10); 1 for the wavegen self-test",
    )
    ap.add_argument(
        "--trigger",
        choices=ERROR_TRIGGERS,
        default="ALLerrors",
        help="fault to trigger (and so screenshot) on (default ALLerrors)",
    )
    ap.add_argument(
        "--seconds", type=float, default=math.inf, help="run time (default: Ctrl-C)"
    )
    ap.add_argument(
        "--interval", type=float, default=1.0, help="seconds between readings"
    )
    ap.add_argument(
        "--no-reset",
        dest="reset",
        action="store_false",
        help="skip the Default Setup (*RST) before setting up",
    )
    ap.add_argument("-o", "--outdir", default=".", help="output directory")
    args = ap.parse_args()

    if not (0 < args.sp < 100 and 0 < args.dsp < 100):
        ap.error("sample points must be between 0 and 100 %")
    if args.probe <= 0:
        ap.error("--probe must be positive")
    if not 1 <= args.channel <= 4:
        ap.error("--channel must be 1-4")
    if args.seconds <= 0 or args.interval <= 0:
        ap.error("--seconds and --interval must be positive")
    level = args.level
    if level is None:
        level = 0.7 if args.polarity == "H-L" else -0.7
    if (level > 0) != (args.polarity == "H-L"):
        ap.error("--level sign must match --polarity (H-L positive, L-H negative)")

    rates = f"{rate_label(args.nominal)}-{rate_label(args.data)}bps"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = run_stem(outdir, f"CAN-FD({args.polarity}) {rates} monitor")

    with KeysightMSO(pyvisa.ResourceManager("@py"), args.scope) as scope:
        probe = scope.prepare_channel(
            args.channel, args.reset, IMPEDANCES[args.impedance], args.probe
        )
        scope.setup_canfd_decode(
            args.channel,
            args.nominal,
            args.data,
            args.sp,
            args.dsp,
            level,
            args.trigger,
        )
        print("probe {}, {:g}:1, {}".format(*probe))
        scope.check_probe(args.channel, probe)
        print(
            f"decoding CAN FD {rates} on CHAN{args.channel} at {level:+g} V, "
            f"triggering on {args.trigger}"
        )
        scope.reset_can_counts()

        def sample(wall_s):
            c = scope.can_counts()
            line = (
                f"{wall_s:7.1f} s  {c['frames']:10d} frames  "
                f"{c['error_frames']:6d} err frames  {c['spec_errors']:6d} spec  "
                f"{c['overload']:4d} ovld  {c['utilization']:5.1f} % bus"
            )
            return [f"{wall_s:.1f}"] + [c[k] for k in FIELDS], line

        rows = poll(args.seconds, args.interval, sample)
        # Freeze the screen on the last triggered (faulty) frame.
        scope.stop()
        save_run(stem, ["wall_s", *FIELDS], rows, scope.get_png())
        # With no fault there was no trigger and nothing to list; the query
        # then never answers.
        if rows and any(rows[-1][i] for i in (2, 3, 4)):
            lister = stem.parent / f"{stem.name}-lister.csv"
            with open(lister, "x") as f:
                f.write(scope.get_lister())
            print(lister)
        print("acquisition stopped on the last fault; press Run to resume")

    if rows and rows[-1][1] == 0 and rows[-1][3] == 0:
        print(
            "no frames decoded: check traffic, --polarity, --level and rates",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
