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

## RF Sensor Calibration

- **cal_rf_sensor** -- RF power sensor calibration utilities
- **rf_sensor_measurements/** -- Calibration data and plots

## License

MIT
