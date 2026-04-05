#!/usr/bin/env python

import dataclasses
import datetime
import time

import pandas as pd
import pyvisa

import rs_smb100a
import hp_436a
import hp_8662a


DEFAULT_RS_SMB100A_SIG_GEN_RESOURCE = "TCPIP::rssmb100a180609.local::INSTR"
DEFAULT_HP_8663A_SIG_GEN_RESOURCE = "TCPIP::e5810a::gpib0,25::INSTR"
DEFAULT_POWER_METER_RESOURCE = "TCPIP::e5810a::gpib0,13::INSTR"


@dataclasses.dataclass
class SensorInfo:
    model: str
    serial: str
    min_dBm: float
    max_dBm: float
    cal_points: list([float, float, float])  # Hz, CF%, Rho


def run(argv, sensor_info, power_levels_dBm):
    rm = pyvisa.ResourceManager("@py")

    # TODO: a better way than this
    if len(argv) >= 2 and "rs" in argv[1]:
        siggy = rs_smb100a.RhodeSchwarzSMB100A(rm, DEFAULT_RS_SMB100A_SIG_GEN_RESOURCE)
        siggy_name = "rssmb100a"
    else:
        siggy = hp_8662a.HP8663A(DEFAULT_HP_8663A_SIG_GEN_RESOURCE)
        siggy_name = "hp8663a"

    print(siggy_name)

    power_meter_resource = DEFAULT_POWER_METER_RESOURCE

    readings = []
    with siggy as siggen:
        siggen.reset()
        siggen.system_preset()
        siggen.set_output(False)

        try:
            with hp_436a.HP436A(power_meter_resource) as pm:
                pm.set_range(pm.Range.Auto)
                pm.set_mode(pm.Mode.dBm)
                pm.set_cal_factor(pm.CalFactor.Disable)

                for power_level_dBm in power_levels_dBm:
                    assert sensor_info.min_dBm <= power_level_dBm <= sensor_info.max_dBm

                    siggen.set_output(False)
                    print("zeroing....")
                    pm.zero_meter()
                    try:
                        siggen.set_power(power_level_dBm)
                    except ValueError:
                        print(f"{power_level_dBm} dBm is out of range")
                        continue

                    siggen.set_output(True)

                    for hz, cf, _rho in sensor_info.cal_points:
                        print(f"frequency set to {hz} Hz")
                        try:
                            siggen.set_frequency(hz)
                        except ValueError:
                            print(f"{hz} Hz is out of range")
                            continue

                        time.sleep(1.0)
                        try:
                            measured_dBm = pm.read_with_settling_dBm()
                        except TimeoutError:
                            print("timeout!")
                            continue
                        corrected_measured_dBm = measured_dBm * (cf / 100.0)
                        print(f"power_level: {power_level_dBm} dBm, ", end="")
                        print(f"corrected: {corrected_measured_dBm:6.2f} dBm, ", end="")
                        abs_err_dB = abs(corrected_measured_dBm - power_level_dBm)
                        print(f"abs_error_dB: {abs_err_dB:4.2f} dB")
                        readings.append(
                            {
                                "hz": hz,
                                "cf": cf,
                                "power_level_dBm": power_level_dBm,
                                "measured_dBm": measured_dBm,
                                "corrected_measured_dBm": corrected_measured_dBm,
                            }
                        )
        finally:
            siggen.set_output(False)

    df = pd.DataFrame(readings)
    print(df)

    yyyymmdd = datetime.datetime.today().strftime("%Y%m%d")
    output_basename = (
        f"sensor_{sensor_info.model}_SN{sensor_info.serial}_{siggy_name}_{yyyymmdd}"
    )
    df.to_csv(output_basename + ".csv", index=False)

    return 0
