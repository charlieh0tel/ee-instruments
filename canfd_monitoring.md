# CAN FD traffic monitoring and error counting

`canfd_monitor.py` uses a Keysight InfiniiVision's CAN FD decoder (DSOX4AUTO
option) as a bus monitor: it sets the decoder up for your bit rates and sample
points, then prints the scope's running totals until Ctrl-C, and saves them.

The scope keeps these totals in hardware, so they cover **all** traffic, not
only the stretches it happens to capture. Checked with the wavegen playing
exactly 10,152 frames/s: 101,497 frames counted in a 10 s window (expected
~101,520).

## Quick start

Same probing as for eye testing (see [canfd_eye_testing.md](canfd_eye_testing.md)):
a differential probe such as the N2792A (50 Ω, 10:1 by default:
`--impedance`, `--probe`), red lead on CAN_L for `--polarity L-H` (dominant
low, the default), on CAN_H for `H-L`.

```sh
R=TCPIP::a-mx4054a-10545.local::5025::SOCKET
# or over USB (needs write access to the device, e.g. a udev rule for 0957):
# R=USB0::0x0957::0x17B2::MY54310545::0::INSTR
uv run ./canfd_monitor.py 1M 2M --sp 75 --dsp 75 --scope $R -o runs/
```

```
   12.0 s      121838 frames       0 err frames       0 spec     0 ovld   82.8 % bus
```

`--seconds N` stops by itself. On exit it saves, with stem
`CAN-FD(H-L) 1M-2Mbps monitor-YYMMDD-HHMMSS` (files are never overwritten; a
clash fails instead):

- `<stem>.csv`: the totals at every reading (`--interval`, default 1 s);
- `<stem>.png`: the screen, frozen on the last frame that hit the trigger;
- `<stem>-lister.csv`: the decoder's frame list for that capture, with the
  error type per frame. Only written if a fault was seen; with none, nothing
  triggered and there is nothing to list.

The acquisition is left stopped so the last fault stays on screen; press Run to
resume.

## The counters

| column | scope query | meaning |
|---|---|---|
| frames | `:SBUS1:CAN:COUNt:TOTal?` | frames decoded |
| error frames | `:COUNt:ERRor?` | error flags sent by nodes that saw a fault |
| spec | `:COUNt:SPEC?` | frames breaking the spec: CRC, stuff, form... |
| ovld | `:COUNt:OVERload?` | overload frames |
| % bus | `:COUNt:UTILization?` | bus utilization |

How a fault is split between "frames" and "spec" depends on the fault. In
testing, a frame whose last CRC bit was flipped counted as both a frame and a
spec error; one with a flipped data bit (so a wrong CRC and stuff count)
counted only as a spec error. So no error rate is computed; compare the
columns instead.

Error frames need other nodes: a receiver that sees a bad frame sends one. On
the self-test loopback nothing is listening, so only spec errors appear there.

## Options

- `--trigger` picks the fault the scope triggers (and so screenshots) on:
  `ALLerrors` (default), `ERRor`, `SPECerror`, `CRCerror`, `STUFferror`,
  `FORMerror`, `ACKerror`, `OVERload`. It doesn't affect the counters.
- `--level` is the decode threshold. The default, ±0.7 V, sits midway between
  the receiver thresholds (0.5 and 0.9 V); its sign must match `--polarity`.
  After a Default Setup the threshold is 0 V, right on the recessive level, and
  the decoder sees nothing (0 frames, 100 % utilization), so it is always set.
- `--impedance` and `--probe` set the input for the differential probe: 50 Ω
  and 10:1 by default; `--impedance 1M --probe 1` for the wavegen self-test
  below.
- `--channel`, `-o`, and `--no-reset` work as in `canfd_eye_test`: it exits
  any eye test, does a Default Setup unless `--no-reset`, turns the other
  channels off, and stops if the probe's reported settings changed.

## Self-test without a bus

With Gen Out cabled to channel 1 (see the eye-testing doc), play a frame and
monitor it. `--no-reset` matters: a Default Setup would turn the wavegen off.

```sh
uv run ./canfd_frame_gen.py 1M 2M --scope $R              # clean
uv run ./canfd_monitor.py 1M 2M --scope $R --no-reset --impedance 1M --probe 1 \
    --seconds 5
uv run ./canfd_frame_gen.py 1M 2M --bad-crc --scope $R    # every frame bad
uv run ./canfd_monitor.py 1M 2M --scope $R --no-reset --impedance 1M --probe 1 \
    --seconds 5
uv run ./canfd_frame_gen.py 1M 2M --off --scope $R
```

Results on an MSO-X 4054A, firmware 07.60, 1M/2M at 75 %/75 %, H-L, 3 s each:

| signal | frames | error frames | spec | % bus |
|---|---|---|---|---|
| clean | 30,459 | 0 | 0 | 82.8 |
| `--bad-crc` | 30,649 | 0 | 30,696 | 82.8 |

and the lister marks each bad frame `CRC`, with CRC `16F51` against the correct
`16F50`.
