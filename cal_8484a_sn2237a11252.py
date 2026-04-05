#!/usr/bin/env python

import sys

import cal_rf_sensor


SENSOR_INFO = cal_rf_sensor.SensorInfo(
    model="8484A",
    serial="2237A11252",
    min_dBm=-65.0,
    max_dBm=-20.0,
    cal_points=[
        # (Hz, Cf%, Rho)
        (0.050e9, 97.0, float("nan")),
        (0.100e9, 95.0, float("nan")),
        (0.500e9, 94.5, float("nan")),
        (1.000e9, 94.0, float("nan")),
        (2.000e9, 93.5, float("nan")),
        (3.000e9, 93.0, float("nan")),
        (4.000e9, 92.5, float("nan")),
        (5.000e9, 92.0, float("nan")),
        (6.000e9, 91.5, float("nan")),
        (7.000e9, 91.0, float("nan")),
        (8.000e9, 91.0, float("nan")),
        (9.000e9, 91.0, float("nan")),
        (10.000e9, 91.0, float("nan")),
        (11.000e9, 91.0, float("nan")),
        (12.000e9, 91.5, float("nan")),
        (13.000e9, 92.0, float("nan")),
        (14.000e9, 92.0, float("nan")),
        (15.000e9, 93.5, float("nan")),
        (16.000e9, 96.0, float("nan")),
        (17.000e9, 96.0, float("nan")),
        (18.000e9, 98.5, float("nan")),
    ],
)


def main(argv):
    return cal_rf_sensor.run(argv, SENSOR_INFO, [-55.0, -45.0, -35.0, -25.0])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
