#!/usr/bin/env python

import sys

import cal_rf_sensor


SENSOR_INFO = cal_rf_sensor.SensorInfo(
    model="8482A",
    serial="2652A12432",
    min_dBm=-30.0,
    max_dBm=20.0,
    cal_points=[
        # (Hz, Cf%, Rho)
        (0.100e6, 97.8, float("nan")),
        (0.300e6, 99.7, float("nan")),
        (1.000e6, 99.4, float("nan")),
        (3.000e6, 98.7, float("nan")),
        (10.000e6, 98.7, float("nan")),
        (30.000e6, 98.3, float("nan")),
        (100.000e6, 97.9, float("nan")),
        (300.000e6, 97.5, float("nan")),
        (1000.000e6, 97.1, float("nan")),
        (2000.000e6, 96.0, float("nan")),
        (3000.000e6, 90.3, float("nan")),
        (4200.000e6, 86.6, float("nan")),
    ],
)


def main(argv):
    return cal_rf_sensor.run(argv, SENSOR_INFO, [-20.0, -10.0, 0.0, 10.0])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
