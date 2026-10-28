---
name: canfd-eye-report
description: Run a CAN FD eye-diagram mask test on the Keysight MSO for a given duration, analyze the eye and the failure statistics, and publish an HTML report artifact with the scope screenshot, the data, the DUT's make/model/serial number and the scope's. Use when the user asks for an eye diagram capture, eye test report, or mask test run with analysis, e.g. "/canfd-eye-report 60" or "run a 2 minute eye test and write it up".
---

# CAN FD eye-test report

Arguments: the duration in seconds (or "2 min" etc.; convert to seconds),
and the DUT's make, model and serial number. Anything else the user gives
(rates, sample points, polarity, probe, resource, DUT notes such as board
revision or firmware) overrides the defaults below.

Everything runs from the repo root (`/home/ch/src/ee-instruments`) with
`uv run`. Scripts used by this skill live next to this file:
`scope_info.py` and `build_report.py`.

## Defaults

- Resource: `USB0::0x0957::0x17B2::MY54310545::0::INSTR` (MSO-X 4054A on USB)
- Test: `1M 2M --sp-min 70 --sp-max 80 --dsp 75`, polarity and probe from the
  tool defaults (L-H, 50 Ω, 10:1 for the N2792A)
- Output directory: `runs-YYYYMMDD` (today's date)

## Scope safety (over USB) - read first

Over USB the 4054A wedges until power-cycled if a query times out or if a
running eye test is turned off over SCPI. So:

- Never send `:MTESt:ENABle 0` yourself, and never send measurement or
  other ad-hoc queries while a mask test is running. The only things to send
  are the steps below.
- If anything times out, stop and tell the user the scope needs a power
  cycle. Do not retry.

## Steps

0. **Identify the DUT.** Nothing on the bus reports the device under test,
   so its make, model and serial number come from the user. If the request
   doesn't give all three, ask for them before touching the scope. If an
   earlier run in this conversation named a DUT, offer to reuse it but
   confirm: the serial number changes when the unit does.

1. **Check the scope.** Run
   `uv run python .claude/skills/canfd-eye-report/scope_info.py RESOURCE`
   and save its JSON as `<outdir>/scope-<HHMMSS>.json`. If `mask_test_on` is
   true and the resource is USB, stop and ask the user to turn the mask test
   off on the front panel (Analyze > Features: Mask). Re-run the check after
   they say it is done.

2. **Run the test.**
   `uv run ./canfd_eye_test.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 -o <outdir> --scope RESOURCE --collect <seconds>`
   Use a Bash timeout of the duration plus 120 s; for runs longer than about
   8 minutes run it in the background and wait for it. The tool prints the
   `.csv` and `.png` paths it saved; use those. If it reports "nothing
   tested", don't build a report: tell the user (likely no traffic, wrong
   polarity or rates) and stop.

3. **Analyze.** Look at the PNG with the Read tool and work through the CSV.
   Cover, from what is actually visible and in the numbers:
   - the verdict: frames tested, failed, failure rate;
   - whether failures are steady or bursty (per-interval rate across the run);
   - recessive and dominant levels (read them against the vertical scale and
     offset shown on screen), and whether there are several dominant levels
     (more than one transmitting node);
   - where the red violations sit on the mask: early or late in the bit,
     upper or lower edge of the keep-out zone, and what that means (late or
     slow edges, low amplitude, noise);
   - ringing, overshoot, or reflections at the edges;
   - how many frames per second were tested versus the bus rate, if known.
   End with what to check next. State only what the screen and data show;
   say so when something can't be told from them.

   Write this as an HTML fragment (`<p>`, `<ul>`, `<h3>`; no `<style>`, no
   classes) to `<stem>-analysis.html`. Plain, direct sentences.

4. **Build the page.**
   ```sh
   uv run python .claude/skills/canfd-eye-report/build_report.py \
       --csv "<stem>.csv" --png "<stem>.png" --info <outdir>/scope-<HHMMSS>.json \
       --dut-make "<make>" --dut-model "<model>" --dut-serial "<serial>" \
       [--dut-note "<board rev, firmware...>"] \
       --analysis "<stem>-analysis.html" \
       --param "Nominal rate=1 Mbit/s" --param "Data rate=2 Mbit/s" \
       --param "Nominal sample point=70-80 %" --param "Data sample point=75 %" \
       --param "Polarity=L-H (dominant low)" --param "Input=50 Ω, probe 10:1" \
       --param "Mask=<the .msk file name>" --param "Duration=<seconds> s" \
       --out "<stem>.html"
   ```
   Set the `--param` values to what was actually run.

5. **Publish.** Call the Artifact tool with `action: "quickstart"` and
   `intent: "other"` first, as its rules require for a new artifact. The
   report is a plain page (its design is fixed in `build_report.py` and
   `report.css`), not one of the listed types. Then publish `<stem>.html`
   with `icon: "chart"` and a one-sentence
   `description` naming the DUT and the result (e.g. "60 s CAN FD 1M/2M
   eye test of <DUT model> SN <serial>: 1.6 % mask failures."). Each run is
   its own artifact.

6. **Report back** in a few lines: the verdict, the main finding, the
   artifact link. Remind the user that the eye test is still running on the
   scope and must be turned off on the front panel before the next run over
   USB.
