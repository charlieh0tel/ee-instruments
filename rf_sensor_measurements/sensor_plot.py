#!/usr/bin/env python

from pathlib import Path
import sys

import pandas as pd
import matplotlib.pyplot as plt


def main(argv):
    for path in argv[1:]:
        path = Path(path)
        df = pd.read_csv(path)

        print(df)

        output_basename = path.stem
        print(output_basename)
        print()

        # Plot measurements.
        plt.figure(figsize=(10, 6))
        for level in df["power_level_dBm"].unique():
            level_df = df[df["power_level_dBm"] == level]
            plt.plot(
                level_df["hz"],
                level_df["corrected_measured_dBm"],
                label=f"{level} dBm",
                marker="x",
            )
        plt.xscale("log")
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Measured Power (Corrected) [dBm]")
        plt.title(output_basename)
        plt.legend()
        plt.grid(True, which="both", ls="--", linewidth=0.5)
        plt.savefig(output_basename + "_measured.png")
        plt.show()

        plt.figure(figsize=(10, 6))
        for level in df["power_level_dBm"].unique():
            level_df = df[df["power_level_dBm"] == level]
            plt.plot(
                level_df["hz"],
                level_df["corrected_measured_dBm"] - level,
                label=f"{level} dBm",
                marker="x",
            )
        plt.xscale("log")
        plt.xlabel("Frequency [Hz]")
        plt.ylabel("Error [dB]")
        plt.title(output_basename)
        plt.legend()
        plt.grid(True, which="both", ls="--", linewidth=0.5)
        plt.savefig(output_basename + "_error.png")
        plt.show()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
