#!/usr/bin/env python

import sys

import cal_rf_sensor


SENSOR_INFO = cal_rf_sensor.SensorInfo(
    model="84841H",
    serial="2703A09730",
    min_dBm=-10.0,
    max_dBm=35.0,
    cal_points=[
        # (Hz, Cf%, Rho)
        (0.1e9, 99.3, 0.012),
        (2.0e9, 98.6, 0.011),
        (3.0e9, 97.4, 0.015),
        (4.0e9, 96.7, 0.010),
        (5.0e9, 96.8, 0.010),
        (6.0e9, 96.9, 0.017),
        (7.0e9, 97.4, 0.019),
        (8.0e9, 97.7, 0.013),
        (9.0e9, 98.0, 0.005),
        (10.0e9, 97.3, 0.011),
        (11.0e9, 96.9, 0.008),
        (12.0e9, 97.6, 0.011),
        (13.0e9, 97.5, 0.019),
        (14.0e9, 95.8, 0.023),
        (15.0e9, 94.9, 0.016),  # CF% obscured, likely correct
        (16.0e9, 95.6, 0.024),  # CF% obscured, maybe correct
        (17.0e9, 94.4, 0.034),  # CF% obscured, only last digit for sure
        (18.0e9, 94.6, 0.037),
    ],  # CF% obscured, only last digit for sure
)


def main(argv):
    return cal_rf_sensor.run(
        argv, SENSOR_INFO, [-5.0, 0.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
