# ee-instruments

Python drivers for lab test equipment over GPIB/VISA/serial.

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
```

Install the pre-commit hook so `ruff check` and `ruff format --check` run on
staged Python files (the same checks CI runs):

```sh
uv run pre-commit install
```

Run any script through uv, which creates/updates `.venv` as needed:

```sh
uv run ./hp_436a.py tcpip::e5810a::gpib0,13::instr
uv run ./cal_8482a_sn2652a12432.py
```

## Instruments

- **hp_8662a** -- HP 8662A signal generator
- **hp_436a** -- HP 436A power meter
- **keithley_2015** -- Keithley 2015 THD multimeter
- **keysight_mso** -- Keysight MSO oscilloscope
- **rs_smb100a** -- Rohde & Schwarz SMB100A signal generator

## Keysight MSO Tools

- **canfd_eye_test** -- CAN FD eye-diagram mask test for any nominal/data bit
  rate (Keysight only ships 500k/4-10M masks); writes `.msk` files, or with
  `--scope` loads the mask and runs the eye test
- **canfd_frame_gen** -- plays valid CAN FD frames (optionally with a fault,
  slow edges, or several senders) on the scope's built-in wavegen, for
  testing without a bus
- **canfd_trigger_check** -- measures the bus's BRS widths, suggests the eye
  test's trigger window, and checks that every trigger lands on a BRS bit
- **canfd_monitor** -- counts CAN FD frames, error frames, spec errors and bus
  utilization with the scope's decoder, until Ctrl-C
- **mso_grab_png** -- writes a screenshot to stdout, fetched from the scope's
  web interface (network only; takes a hostname, not a VISA resource):
  `uv run ./mso_grab_png.py [host] > screen.png`

Pass the scope with `--scope RESOURCE`:

- network: `TCPIP::<host>::5025::SOCKET` (VXI-11 `::INSTR` is unreliable)
- USB: `USB0::0x0957::0x17B2::<serial>::0::INSTR`. On Linux this needs a udev
  rule; before a run, turn any running eye test off on the front panel.

```sh
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0957", MODE="0666"' |
    sudo tee /etc/udev/rules.d/99-keysight.rules
sudo udevadm control --reload && sudo udevadm trigger
```

See [canfd_eye_testing.md](canfd_eye_testing.md) (eye masks: usage, how the
trigger window is derived, what was verified on the scope) and
[canfd_monitoring.md](canfd_monitoring.md) (traffic and error counting).

## RF Sensor Calibration

- **cal_rf_sensor** -- RF power sensor calibration utilities
- **rf_sensor_measurements/** -- Calibration data and plots

## License

MIT
