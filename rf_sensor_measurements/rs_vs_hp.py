#!/usr/bin/env python

from pathlib import Path
import re
import sys

import matplotlib.pyplot as plt
import pandas as pd


_RE = (
    r"^(sensor)_([a-zA-Z0-9]+)_(SN[a-zA-Z0-9]+)_([a-zA-Z0-9]+)_(\d{8})\.([a-zA-Z9-9]+)$"
)


def main(argv):
    globs = set()
    for csv in Path(".").glob("*.csv"):
        match = re.match(_RE, csv.name)
        if not match:
            continue
        model = match.group(2)
        sn = match.group(3)
        yyyymmdd = match.group(5)
        globs.add(f"sensor_{model}_{sn}_*_{yyyymmdd}.csv")

    for glob in globs:
        csvs = list(Path(".").glob(glob))
        dfs = []
        for csv in csvs:
            match = re.match(_RE, csv.name)
            siggen = match.group(4)
            df = pd.read_csv(csv)
            df["siggen"] = siggen
            dfs.append(df)
        combined_df = pd.concat(dfs, ignore_index=True)
        match = re.match(_RE, csv.name)
        model = match.group(2)
        sn = match.group(3)
        yyyymmdd = match.group(5)
        print(f"{model} {sn} {yyyymmdd}: {len(csvs)}")

        outname = f"{model}_{sn}_{yyyymmdd}"
        combined_df.to_parquet(f"{outname}.parquet")

        siggens = sorted(combined_df["siggen"].unique())
        assert len(siggens) == 2
        siggen1 = siggens[0]
        siggen2 = siggens[1]

        df_pivot = combined_df.pivot_table(
            index=["hz", "power_level_dBm"],
            columns="siggen",
            values="corrected_measured_dBm",
        )

        df_pivot["difference_dB"] = df_pivot[siggen1] - df_pivot[siggen2]
        df_pivot.dropna(inplace=True)

        # Reset index to make 'hz' and 'power_level_dBm' columns again.
        df_pivot = df_pivot.reset_index()

        print(df_pivot)
        power_levels = df_pivot["power_level_dBm"].unique()

        plt.figure(figsize=(15, 10))
        for level in power_levels:
            df_subset = df_pivot[df_pivot["power_level_dBm"] == level]
            plt.plot(
                df_subset["hz"],
                df_subset["difference_dB"],
                label=f"Power Level: {level} dBm",
                marker="x",
            )
            max_abs_err = df_subset["difference_dB"].abs().max()
            x = df_subset.loc[df_subset["difference_dB"].abs().idxmax(), "hz"]
            y = df_subset.loc[
                df_subset["difference_dB"].abs().idxmax(), "difference_dB"
            ]
            x_text = 1.1 * x
            y_text = y + 0.1 * max_abs_err
            plt.annotate(
                f"Max Abs Err: {max_abs_err:.2f} dB",
                xy=(x, y),
                xytext=(x_text, y_text),
                arrowprops=dict(facecolor="red", shrink=0.02),
            )

        plt.xscale("log")
        plt.xlabel("Frequency [Hz]")
        plt.ylabel(f"({siggen1} - {siggen2}) [dB]")
        plt.suptitle(outname)
        plt.title("Difference in Corrected Measured dBm vs. Frequency")
        plt.legend(title="Power Level")
        plt.grid(True, which="both", ls="--", linewidth=0.5)

        plt.savefig(outname + "_siggen_compare.png")

        plt.show()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
