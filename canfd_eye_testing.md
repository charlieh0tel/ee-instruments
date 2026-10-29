# CAN FD eye-diagram mask testing

Keysight InfiniiVision 3000T / 4000 X-Series scopes can run a CAN FD eye
diagram test: they trigger on the bit-rate switch (BRS) bit, overlay the first
10 data-phase bits of every frame aligned the way a receiver resynchronizes,
and check them against a pass/fail mask (Keysight app note 5992-0437EN,
["CAN FD Eye-Diagram Mask Testing"](https://www.keysight.com/us/en/assets/7018-04704/application-notes/5992-0437.pdf)). It needs the DSOX4AUTO (CAN FD) and DSOX4MASK
options and firmware 4.05 or later.

Keysight only publishes masks for a 500 kbps nominal rate with 4-10 Mbps data.
`canfd_eye_test.py` makes them for any rates and can load them and run the test
over SCPI. `canfd_frame_gen.py` plays a CAN FD frame on the scope's own wavegen
so the whole thing can be checked without a bus.

## Quick start: real bus

Probe with a differential probe. The tools set the channel up for it after
the Default Setup, which leaves 1 MΩ and 1:1: `--impedance` (default 50 Ω) and
`--probe` attenuation (default 10 for 10:1). The defaults suit the N2792A
(10:1, needs 50 Ω), which the scope can't identify (no AutoProbe) and which
takes its own power (9 V battery or USB). Red lead on CAN_L shows dominant
bits low: `--polarity L-H`, the default (how our fixture is wired). Red lead
on CAN_H shows them high: `H-L`.

```sh
R=TCPIP::a-mx4054a-10545.local::5025::SOCKET
uv run ./canfd_eye_test.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 \
    -o runs/ --scope $R --collect
```

This writes the `.msk` file, puts the scope in Default Setup with only the
probed channel on, loads the mask, starts the eye test, and prints the running
totals until Ctrl-C (or give `--collect SECONDS`). It then saves
`runs/CAN-FD(L-H) 1M-2Mbps-YYMMDD-HHMMSS.csv` (totals once a second) and `.png`
(the screen: eye, mask, statistics) and leaves the test running on the scope.
Over USB, turn it off on the front panel (Analyze > Features: Mask) before the
next run: exiting it over USB wedges the scope, so the tools refuse to start
while it is on. Over the network they turn it off themselves.

Other modes: no `--scope` just writes `.msk` files for a USB stick (both
polarities unless one is given); `--scope` without `--start`/`--collect` loads
the mask but does not start the test; `--no-reset` skips the Default Setup
(impedance and attenuation are still set).

The upload reads the channel's probe ID and attenuation before the reset and
again after loading, and stops with an error if they changed or the impedance
or attenuation isn't the one asked for, so an
AutoProbe's settings can't be silently undone. (Checked only with no probe
attached so far.)

## Choosing the timing arguments

The trigger is a pulse-width trigger on the recessive BRS bit. The rate switches
at BRS's sample point, so that pulse lasts

    t_BRS = sp × t_nominal + (1 − dsp) × t_data

At 1M/2M with both sample points at 75 %: 750 + 125 = **875 ns**. `--sp-min`
and `--sp-max` set the window through the nominal sample point, `--dsp` is the
data-phase sample point. Keysight's files use 75-87.5 % and ignore the data
term, which is only 25 ns at 10 Mbps; at 2 Mbps it is 125 ns, enough to miss
the pulse entirely.

The window must stay clear of two other recessive pulses, and the generator
refuses one that doesn't:

- a lone nominal recessive bit, 1 µs at 1 Mbps: `--sp-max` too high;
- the CRC delimiter, dsp × t_data + (1 − sp) × t_nominal = 625 ns here:
  `--sp-min` too low.

| bus sample points | arguments | BRS | window |
|---|---|---|---|
| 75 % / 75 % | `--sp-min 70 --sp-max 80 --dsp 75` | 875 ns | 825-925 ns |
| 80 % / 75 % | `--sp-min 75 --sp-max 85 --dsp 75` | 925 ns | 875-975 ns |

The defaults (`--sp-min 75 --sp-max 87.5`) suit 500 kbps; at 1 Mbps they reach
a whole bit and are rejected.

## Setting the window from the bus

The computed window assumes ideal edges. On a real bus the
dominant-to-recessive edge is not driven: it decays through the termination,
so every recessive pulse is shorter at the trigger level than its bit time.
At about 60 ns of shortening the BRS falls below the window and 1 us bits
fall into it; the eye test then overlays the wrong bits and fails a clean
signal. On the wavegen, a frame with 100 ns recessive-going ramps failed
4 % of tests with the computed window; nodes with different edge speeds or
sample points make it worse.

Measure the bus and set the window from it, with the mask test off:

```sh
uv run ./canfd_trigger_check.py segments 1M 2M --trigger sof --scope $R -o runs
uv run ./canfd_trigger_check.py suggest runs/trig-sof-....npz --level 0.7
uv run ./canfd_trigger_check.py segments 1M 2M --trigger glitch \
    --window LO-HI --level L --scope $R -o runs
uv run ./canfd_eye_test.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 \
    --window LO-HI --level L --scope $R --collect 60
```

`suggest` prints each ID's BRS width and the nearest other recessive pulses,
and proposes a window, or says none is safe at that level. Raising the level
toward the dominant level shortens the pulses less; try 1.0, 1.2, 1.4 V, but
keep it well below the weakest node's dominant level or its frames are
missed. The glitch-triggered check labels every capture BRS or wrong; it
must report no wrong triggers.

On the wavegen, two senders (BRS 800 and 900 ns, 150 and 50 ns ramps): the
computed window put half the triggers on the wrong bits and the eye test
failed 6 %; `--window 700-897 --level 1.4` from `suggest` gave 500 of 500
triggers on BRS and 0 failures.

## The mask

Each of the 10 bits gets a hexagon from 30 % to 80 % of the bit time between
0.5 V and 0.9 V (the ISO 11898-2 receiver thresholds: ≤ 0.5 V must read
recessive, ≥ 0.9 V dominant). It sits low in a 0-2 V eye because the thresholds
do; real transmitters drive 1.5-3 V dominant, leaving more margin above. This
geometry does not depend on bit rate and is copied unchanged from Keysight's
files; only the mask's time span, the trigger window and the sequencer rate
change. `canfd_eye_test.py --verify DIR` regenerates Keysight's ten 500k files
and compares them numerically (all match).

## Self-test without a bus

Cable the wavegen output (Gen Out) to the channel with a BNC. The wavegen's
50 Ω source is set for a high-Z load, so the channel must be 1 MΩ
(`--impedance 1M`, and `--probe 1`: no probe) for it to deliver 0-2 V; no
terminator needed for a short
cable.

```sh
R=TCPIP::a-mx4054a-10545.local::5025::SOCKET
uv run ./canfd_frame_gen.py 1M 2M --sp 75 --dsp 75 --polarity H-L --scope $R
uv run ./canfd_eye_test.py 1M 2M --sp-min 70 --sp-max 80 --dsp 75 \
    --polarity H-L --scope $R --no-reset --impedance 1M --probe 1 \
    --collect 10
uv run ./canfd_frame_gen.py 1M 2M --off --scope $R
```

Order matters: `canfd_eye_test`'s Default Setup would turn the wavegen off, so
play the frame first and load the mask with `--no-reset`. `--off` turns the
eye test off before the wavegen.

The frame is a real ISO CAN FD frame (stuffing, stuff count, CRC-17, rate
switch at the sample points); the scope's CAN FD decoder accepts it. `--dip N`
adds a 0.6 V dip in the middle of data-phase bit N (1 = ESI), inside the mask,
to see failures.

Results on an MSO-X 4054A, firmware 07.60, 1M/2M at 75 %/75 %, H-L:

| signal | tested | failed |
|---|---|---|
| clean frame | 13,715 | 0 |
| `--dip 1` | 12,137 | 3,164 (26 %) |
| dominant level 0.8 V | 0 | 0 |

The dip fails about a quarter, not all, of the tests: it is in one of the 10
overlaid bits, so a "test" is presumably not a whole frame. A dominant level
of 0.8 V barely crosses the 0.7 V trigger level, so nothing triggers and
nothing is tested: a test count stuck at 0 on a real bus means no BRS triggers
(signal level, polarity or timing arguments), not a pass.

## Scope behavior worth knowing

Seen on the MSO-X 4054A, firmware 07.60:

- **Use a `::5025::SOCKET` resource.** The VXI-11 (`::INSTR`) server refuses
  connections now and then, once for over six seconds; raw SCPI has been
  reliable. `KeysightMSO.open()` retries either way.
- **The scope can wedge** (no SCPI, no web server, power cycle needed). It
  did twice: when the `:MTES:TSEQ:CAN:FD` line was sent inside the mask
  upload, and when `*RST` arrived while an eye test was running. The tools
  therefore send that line on its own and turn the mask test off
  (`:MTESt:ENABle 0`, the app note's way out of the test) before any reset.
  Starting the test with no signal present did not wedge it; it just tested
  nothing. Over USB, `:MTESt:ENABle 0` on an eye test running with real
  traffic never answered (30 s) and wedged it, every time; the tools refuse
  instead (see above). The scope may also restore a running test at power-up.
  Over USB, any query that times out also leaves the USB interface dead until
  a reboot (pyvisa-py's USBTMC abort fails), so the tools use a 30 s timeout.
- `:MTESt:DATA <block>` loads a `.msk` file's contents, `:MTESt:DATA?` returns
  them. Over SCPI the `%d` channel placeholders in the setup section must be
  filled in by the sender.
- The sequencer accepts data rates outside Keysight's 4-10 Mbps (1, 2 and
  3 Mbps tried); a nonsense value gets `-222 Data out of range`.
- `:MTES:TSEQ:CAN:FD` does nothing unless mask testing is enabled.
- Counters: `:MTESt:COUNt:WAVeforms?`, `:FWAVeforms?`, `:TIME?`, reset with
  `:MTESt:COUNt:RESet`. The scope's test time runs slower than the wall clock,
  so the CSV records both.
- Screenshots take longer than pyvisa's 2 s default timeout while the test
  runs; `get_png()` allows 20 s.
- The wavegen took a 3,940-point arbitrary waveform; `canfd_frame_gen` caps
  frames at 4,000 points, which rules out some slow-rate combinations (e.g.
  500k/5M at 80 %/70 %). The real limit may be higher.
