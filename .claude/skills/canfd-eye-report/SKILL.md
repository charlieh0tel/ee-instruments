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
   - the verdict: frames tested, failed, failure rate. Say what the rate
     means: the share of captured waveforms that touched a keep-out zone at
     least once. It is not a bit or frame error rate; relate it to decoder
     results (e.g. a monitor run's protocol errors) if any are known. Call it
     a verdict on the bus, not the DUT, unless only the DUT transmits;
   - whether failures are steady or bursty: per-interval rate across the
     run, per-minute (or 30 s) rates, and whether the interval-to-interval
     scatter is more than counting noise (sd of a binomial rate at that
     interval's frame count);
   - recessive and dominant levels (read them against the vertical scale and
     offset shown on screen), and whether there are several dominant levels
     (more than one transmitting node);
   - where the red violations sit on the mask. Look at every keep-out zone
     and describe each red cluster: its position along the zone (start,
     inside, end), upper or lower edge, which transition direction runs
     through it, and what that means (late or slow edges, low amplitude,
     noise). Don't generalize from one cluster;
   - ringing, overshoot, or reflections at the edges;
   - alignment: the eye is only valid if every capture starts on the BRS bit.
     Note the trigger window the run printed, which pulses it excludes, and
     that the screen alone doesn't confirm alignment. A consistent early or
     late edge pattern could also come from misaligned captures;
   - how many frames per second were tested versus the bus rate, if known.
   End with what to check next. Always consider: finding which node sends
   the failing frames (stop on mask failure, read the ID from the decoder
   list), capturing one failing waveform to confirm BRS alignment,
   termination (about 60 Ω across CAN_H/CAN_L, bus off) and stubs, and
   single-ended CAN_H and CAN_L measurements to tell a slow edge on one wire
   from a common-mode problem. State only what the screen and data show;
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
       --param "Polarity=L-H (dominant low)" \
       --param "Probe=N2792A differential, 10:1, high impedance at the bus" \
       --param "Scope input=50 Ω (terminates the probe output)" \
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
