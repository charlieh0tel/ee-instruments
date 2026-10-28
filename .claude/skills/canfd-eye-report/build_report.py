#!/usr/bin/env python

"""Build a self-contained HTML report for one canfd_eye_test --collect run.

Usage:
  build_report.py --csv RUN.csv --png RUN.png --info scope.json \\
      --dut-make MAKE --dut-model MODEL --dut-serial SN \\
      --analysis analysis.html --param "Nominal rate=1 Mbit/s" ... --out RUN.html

--dut-* identify the device under test (from the user; nothing on the bus
reports them). --info is scope_info.py's JSON. --analysis is an HTML fragment
(<p>, <ul>, <h3>...) written for this run. Each --param is a "Label=value" row for the
test-setup table, in the order given.
"""

import argparse
import base64
import csv
import html
import json
import math
from datetime import datetime
from pathlib import Path

CSS = (Path(__file__).parent / "report.css").read_text()

FONTS = (
    "https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@500;600"
    "&family=JetBrains+Mono:wght@400;500;600&family=Source+Sans+3:wght@400;600"
    "&display=swap"
)


def esc(s):
    return html.escape(str(s))


def load_rows(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = [
        {
            "wall_s": float(r["wall_s"]),
            "test_s": float(r["test_s"]),
            "tested": int(r["tested"]),
            "failed": int(r["failed"]),
            "rate": float(r["failure_rate"]),
        }
        for r in rows
    ]
    # The last sample can repeat the previous one when the run ends on a
    # poll boundary; drop exact duplicates.
    dedup = []
    for r in out:
        if not dedup or (r["tested"], r["wall_s"]) != (
            dedup[-1]["tested"],
            dedup[-1]["wall_s"],
        ):
            dedup.append(r)
    return dedup


def interval_rates(rows):
    """(wall_s, per-interval failure rate %) where frames were tested."""
    pts, prev = [], None
    for r in rows:
        if prev is not None:
            dt = r["tested"] - prev["tested"]
            if dt > 0:
                pts.append((r["wall_s"], 100 * (r["failed"] - prev["failed"]) / dt))
        prev = r
    return pts


def nice_max(v):
    if v <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def chart_svg(rows):
    inter = interval_rates(rows)
    cumul = [(r["wall_s"], 100 * r["rate"]) for r in rows if r["tested"] > 0]
    if not cumul:
        return "<p>No frames were tested, so there is no trend to plot.</p>"
    W, H, L, R, T, B = 720, 260, 56, 16, 14, 40
    x_max = max(r["wall_s"] for r in rows) or 1.0
    y_max = nice_max(max([p[1] for p in inter + cumul] + [0.0]) * 1.1)

    def x(v):
        return L + (W - L - R) * v / x_max

    def y(v):
        return T + (H - T - B) * (1 - v / y_max)

    parts = [
        (
            f'<svg viewBox="0 0 {W} {H}" role="img" '
            'aria-label="Failure rate over the run">'
        )
    ]
    for i in range(6):
        v = y_max * i / 5
        parts.append(
            f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
            f'stroke="var(--rule)" stroke-width="1" fill="none"/>'
        )
        parts.append(
            f'<text x="{L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:g}</text>'
        )
    step = nice_max(x_max / 6)
    t = 0.0
    while t <= x_max + 1e-9:
        parts.append(
            f'<text x="{x(t):.1f}" y="{H - B + 18}" text-anchor="middle">{t:g}</text>'
        )
        t += step
    parts.append(
        f'<text x="{(L + W - R) / 2}" y="{H - 4}" text-anchor="middle">'
        "elapsed time, s</text>"
    )
    parts.append(
        f'<text x="14" y="{(T + H - B) / 2}" text-anchor="middle" '
        f'transform="rotate(-90 14 {(T + H - B) / 2})">failure rate, %</text>'
    )
    if inter:
        pts = " ".join(f"{x(a):.1f},{y(b):.1f}" for a, b in inter)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="var(--series2)" '
            'stroke-width="1.2" stroke-opacity="0.8"/>'
        )
    pts = " ".join(f"{x(a):.1f},{y(b):.1f}" for a, b in cumul)
    parts.append(
        f'<polyline points="{pts}" fill="none" stroke="var(--accent)" '
        'stroke-width="2.4" stroke-linejoin="round"/>'
    )
    ex, ey = cumul[-1]
    parts.append(
        f'<circle cx="{x(ex):.1f}" cy="{y(ey):.1f}" r="4" fill="var(--accent)"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--png", required=True)
    ap.add_argument("--info", required=True, help="scope_info.py JSON")
    ap.add_argument("--analysis", required=True, help="HTML fragment")
    ap.add_argument("--param", action="append", default=[], metavar="LABEL=VALUE")
    ap.add_argument("--dut-make", required=True)
    ap.add_argument("--dut-model", required=True)
    ap.add_argument("--dut-serial", required=True)
    ap.add_argument("--dut-note", default="", help="e.g. board rev or firmware")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = load_rows(args.csv)
    info = json.loads(Path(args.info).read_text())
    png64 = base64.b64encode(Path(args.png).read_bytes()).decode("ascii")
    analysis = Path(args.analysis).read_text()
    params = [p.split("=", 1) for p in args.param]

    empty = {"tested": 0, "failed": 0, "rate": 0.0, "test_s": 0.0, "wall_s": 0.0}
    last = rows[-1] if rows else empty
    tested, failed = last["tested"], last["failed"]
    if tested == 0:
        verdict, vclass = "No frames tested", "none"
    elif failed == 0:
        verdict, vclass = "Pass: no mask violations", "pass"
    else:
        verdict, vclass = f"Fail: {failed:,} mask violations", "fail"

    run_name = Path(args.csv).stem
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    def ident(pairs):
        return "".join(
            f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in pairs if v
        )

    dut = ident(
        (
            ("Make", args.dut_make),
            ("Model", args.dut_model),
            ("Serial number", args.dut_serial),
            ("Notes", args.dut_note),
        )
    )
    scope = ident(
        (
            ("Make", info.get("make", "")),
            ("Model", info.get("model", "")),
            ("Serial number", info.get("serial", "")),
            ("Firmware", info.get("firmware", "")),
        )
    )
    stats = [
        ("Failure rate", f"{100 * last['rate']:.3f} %", True),
        ("Frames tested", f"{tested:,}", False),
        ("Frames failed", f"{failed:,}", False),
        ("Test time (scope)", f"{last['test_s']:.1f} s", False),
        ("Wall time", f"{last['wall_s']:.1f} s", False),
    ]
    stats_html = "".join(
        f'<div class="stat{" key" if key else ""}"><span class="v">{esc(v)}</span>'
        f'<span class="l">{esc(lbl)}</span></div>'
        for lbl, v, key in stats
    )
    setup = [("VISA resource", info.get("resource", ""))] + [
        (k.strip(), v.strip()) for k, v in params
    ]
    setup_html = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in setup)
    table_rows = "".join(
        "<tr>"
        f"<td>{r['wall_s']:.1f}</td><td>{r['test_s']:.1f}</td>"
        f"<td>{r['tested']:,}</td><td>{r['failed']:,}</td>"
        f"<td>{100 * r['rate']:.4f}</td></tr>"
        for r in rows
    )

    page = f"""<title>{esc(args.dut_model)} {esc(args.dut_serial)} Eye Test</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>
<main>
  <header>
    <span class="eyebrow">CAN FD eye-diagram mask test · {esc(run_name)}</span>
    <h1>{esc(args.dut_make)} {esc(args.dut_model)}, SN {esc(args.dut_serial)}</h1>
    <span class="verdict {vclass}">{esc(verdict)}</span>
  </header>

  <section aria-labelledby="h-dut">
    <h2 id="h-dut">Device under test</h2>
    <dl class="ident">{dut}</dl>
  </section>

  <section aria-labelledby="h-sum">
    <h2 id="h-sum">Result</h2>
    <div class="stats">{stats_html}</div>
  </section>

  <section aria-labelledby="h-screen">
    <h2 id="h-screen">Scope screen at end of run</h2>
    <figure>
      <img src="data:image/png;base64,{png64}"
        alt="Oscilloscope screen: CAN FD eye diagram, mask and test statistics">
      <figcaption>Captured over SCPI when the run ended. Grey shapes are the
      mask keep-out zones; red marks are samples that violated them.</figcaption>
    </figure>
  </section>

  <section aria-labelledby="h-trend">
    <h2 id="h-trend">Failure rate over the run</h2>
    <div class="chart">{chart_svg(rows)}</div>
    <div class="legend">
      <span><i class="swatch" style="background: var(--accent)"></i>cumulative</span>
      <span><i class="swatch" style="background: var(--series2)"></i>per interval</span>
    </div>
  </section>

  <section aria-labelledby="h-analysis" class="analysis">
    <h2 id="h-analysis">Analysis</h2>
    {analysis}
  </section>

  <section aria-labelledby="h-setup">
    <h2 id="h-setup">Test setup</h2>
    <dl class="kv">{setup_html}</dl>
  </section>

  <section aria-labelledby="h-inst">
    <h2 id="h-inst">Test instrument</h2>
    <dl class="ident">{scope}</dl>
  </section>

  <section aria-labelledby="h-data">
    <h2 id="h-data">Data</h2>
    <p>Mask-test totals read from the scope at each interval
    (<code>{esc(Path(args.csv).name)}</code>).</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Wall, s</th><th>Test, s</th><th>Tested</th>
          <th>Failed</th><th>Failure rate, %</th></tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
  </section>

  <footer>{esc(info.get("idn", ""))} · report generated {esc(generated)}</footer>
</main>
"""
    Path(args.out).write_text(page)
    print(args.out)


if __name__ == "__main__":
    main()
