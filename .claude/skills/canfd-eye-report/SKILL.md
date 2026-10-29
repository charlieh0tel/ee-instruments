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

- Resource: `USB0::0x0957::0x17B2::MY54310545::0::INSTR` (MSO-X 4054A on
  USB). If step 1 fails with "No device found" (the PC doesn't see it on
  USB), use the network: `TCPIP::a-mx4054a-10545::5025::SOCKET`. If neither
  answers, stop and ask the user to check the scope.
- Test: `1M 2M --sp-min 70 --sp-max 80 --dsp 75`, polarity and probe from the
  tool defaults (L-H, `--impedance 50`, `--probe 10`)
- Probe: Keysight N2792A differential probe, the one normally used. 10:1,
  high impedance at the bus (it is what loads the CAN bus); its output needs
  the scope's 50 Ω input. It has no AutoProbe, so the scope reports probe
  `NONE` and the tools set ratio and impedance; it takes its own power. If
  the user names a different probe, set `--probe` and `--impedance` to suit
  it and describe that probe in the report instead.
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
   confirm: the serial number changes when the unit does. Also take any
   bench measurements the user gives (termination resistance, cable length,
   node count, stub lengths); don't hold up the run asking for them.

1. **Check the scope.** Run
   `uv run python .claude/skills/canfd-eye-report/scope_info.py RESOURCE`
   and save its JSON as `<outdir>/scope-<HHMMSS>.json`. If `mask_test_on` is
   true and the resource is USB, stop and ask the user to turn the mask test
   off on the front panel (Analyze > Features: Mask). Re-run the check after
   they say it is done.

2. **Set the trigger from the bus, then check it.** The eye is only valid
   if every capture starts on a BRS bit. The default window (825-925 ns at
   -0.7 V) assumes ideal edges; on a real bus the slow dominant-to-recessive
   edge shortens every recessive pulse, which can push BRS out of the window
   and 1 us bits into it. Then the eye test overlays the wrong bits and
   reports failures that aren't in the signal. With the mask test off:
   - Measure: `uv run ./canfd_trigger_check.py segments 1M 2M --trigger sof --segments 500 --scope RESOURCE -o <outdir>`.
     It prints each ID's BRS width; keep the `.npz` path.
   - Choose: `uv run ./canfd_trigger_check.py suggest <npz> --auto`. It
     measures each ID's dominant level, tries trigger levels from 0.7 V up
     to 70 % of the weakest one, and prints `--window LO-HI --level L` for
     the lowest level with a safe window. If it finds none, stop: say so,
     with the BRS widths, dominant levels and nearest other pulses it
     printed.
   - Check: `uv run ./canfd_trigger_check.py segments 1M 2M --trigger glitch --window LO-HI --level L --segments 500 --scope RESOURCE -o <outdir>`.
     It must report 0 wrong. Ambiguous segments (frame start outside the
     record) are allowed but go in the report. Any wrong trigger: stop and
     report it; don't run the eye test.

   These captures have run over the network; over USB they have not been
   tried yet. They avoid the commands known to wedge the scope (see Scope
   safety), but stop at the first timeout.

3. **Run the test.**
   `uv run ./canfd_eye_test.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 --window LO-HI --level L -o <outdir> --scope RESOURCE --collect <seconds>`
   Use a Bash timeout of the duration plus 120 s; for runs longer than about
   8 minutes run it in the background and wait for it. The tool prints the
   `.csv` and `.png` paths it saved; use those. If it reports "nothing
   tested", don't build a report: tell the user (likely no traffic, wrong
   polarity or rates) and stop.

4. **Analyze.** Look at the PNG with the Read tool and work through the CSV.
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
   - alignment: give the trigger window and level, each ID's measured BRS
     width, and the step-2 check (wrong and ambiguous counts out of N, and
     the 95 % upper bound 3/N on wrong triggers). Fast, driven
     (recessive-to-dominant) edges crossing mid-bit mean misaligned
     captures, not a slow bus;
   - how many frames per second were tested versus the bus rate, if known.
   End with what to check next. Always consider: finding which node sends
   the failing frames (stop on mask failure, read the ID from the decoder
   list), capturing one failing waveform to confirm BRS alignment,
   termination (about 60 Ω across CAN_H/CAN_L, bus off; if the user
   measured it, use the value instead of suggesting it: at 60 Ω look to
   stubs, connectors and bus capacitance for ringing and slow recessive
   edges) and stubs, and
   single-ended CAN_H and CAN_L measurements to tell a slow edge on one wire
   from a common-mode problem. State only what the screen and data show;
   say so when something can't be told from them.

   Write this as an HTML fragment (`<p>`, `<ul>`, `<h3>`; no `<style>`, no
   classes) to `<stem>-analysis.html`. Plain, direct sentences.

5. **Build the page.**
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
       --param "Trigger=<LO-HI> ns at <L> V, set from measured BRS widths" \
       --param "Trigger check=<brs> of <N> on BRS, <wrong> wrong" \
       --out "<stem>.html"
   ```
   Set the `--param` values to what was actually run, and add one per bench
   measurement the user gave, e.g.
   `--param "Termination=60 Ω measured (CAN_H to CAN_L, bus off)"`.

6. **Publish.** Call the Artifact tool with `action: "quickstart"` and
   `intent: "other"` first, as its rules require for a new artifact. The
   report is a plain page (its design is fixed in `build_report.py` and
   `report.css`), not one of the listed types. Then publish `<stem>.html`
   with `icon: "chart"` and a one-sentence
   `description` naming the DUT and the result (e.g. "60 s CAN FD 1M/2M
   eye test of <DUT model> SN <serial>: 1.6 % mask failures."). Each run is
   its own artifact.

7. **Report back** in a few lines: the verdict, the main finding, the
   artifact link. Remind the user that the eye test is still running on the
   scope and must be turned off on the front panel before the next run over
   USB.
