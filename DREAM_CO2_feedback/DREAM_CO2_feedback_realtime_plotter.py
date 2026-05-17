#!/usr/bin/env python3
"""
DREAM_CO2_feedback_realtime_plotter.py

Real-time dynamic plotter for DREAM CO2 feedback CSV logs.

Default view:
    - Last 10 minutes

Button:
    - Toggle between "Last 10 min" and "Past 24 h"

X-axis:
    - Time of day in HH:MM format

Panels:
    1. li850_co2_ppm, co2_avg_ppm, target_co2_ppm
    2. mfc_setpoint_mln_min, mfc_actual_mln_min + secondary axis for co2_supply_avg_umol_s
    3. dCdt_regression_ppm_s and dCdt_simple_ppm_s
    4. A_regression_umol_s, A_regression_smoothed_umol_s, NE_regression_umol_s
    5. A_from_avg_supply_umol_s, A_smoothed_umol_s
    6. A_MFC_only_umol_s, A_MFC_only_smoothed_umol_s

Notes:
    - Axis labels use common, easy-to-understand names with units on a second line.
    - Legends use short scientific names.
    - If NE_regression_umol_s is missing from an older log, it is derived as:
          NE_regression_umol_s = -A_regression_umol_s
      or, if available:
          NE_regression_umol_s = storage_regression_umol_s - co2_supply_avg_umol_s

Run:
    python DREAM_CO2_feedback_realtime_plotter.py --folder DREAM_CO2_logs --latest --interval 5
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.animation import FuncAnimation
from matplotlib.artist import Artist
from matplotlib.ticker import MaxNLocator, ScalarFormatter
from matplotlib.widgets import Button


# ============================================================
# Column settings
# ============================================================

TIME_COL = "timestamp"
ELAPSED_COL = "elapsed_s"

PANEL1_CO2_COLS = [
    "li850_co2_ppm",
    "co2_avg_ppm",
    "target_co2_ppm",
]

PANEL2_MFC_COLS = [
    "mfc_setpoint_mln_min",
    "mfc_actual_mln_min",
]

PANEL2_SUPPLY_COLS = [
    "co2_supply_avg_umol_s",
]

PANEL3_DCDT_COLS = [
    "dCdt_regression_ppm_s",
    "dCdt_simple_ppm_s",
]

PANEL4_A_NE_COLS = [
    "A_regression_umol_s",
    "A_regression_smoothed_umol_s",
    "NE_regression_umol_s",
]

PANEL5_A_AVG_COLS = [
    "A_from_avg_supply_umol_s",
    "A_smoothed_umol_s",
]

PANEL6_A_MFC_COLS = [
    "A_MFC_only_umol_s",
    "A_MFC_only_smoothed_umol_s",
]

ALL_DATA_COLS = (
    PANEL1_CO2_COLS
    + PANEL2_MFC_COLS
    + PANEL2_SUPPLY_COLS
    + PANEL3_DCDT_COLS
    + PANEL4_A_NE_COLS
    + PANEL5_A_AVG_COLS
    + PANEL6_A_MFC_COLS
    + [
        "storage_regression_umol_s",
        "co2_supply_avg_umol_s",
    ]
)

# Short scientific names used in legends
LABELS = {
    "li850_co2_ppm": "C_LI850",
    "co2_avg_ppm": "C_avg",
    "target_co2_ppm": "C_target",

    "mfc_setpoint_mln_min": "F_MFC,set",
    "mfc_actual_mln_min": "F_MFC,act",
    "co2_supply_avg_umol_s": "F_CO2,avg",

    "dCdt_regression_ppm_s": "dC/dt_reg",
    "dCdt_simple_ppm_s": "dC/dt_raw",

    "A_regression_umol_s": "A_reg",
    "A_regression_smoothed_umol_s": "A_reg,smooth",
    "NE_regression_umol_s": "NE_reg",

    "A_from_avg_supply_umol_s": "A_avg",
    "A_smoothed_umol_s": "A_smooth",

    "A_MFC_only_umol_s": "A_MFC",
    "A_MFC_only_smoothed_umol_s": "A_MFC,smooth",
}

# Common names used as y-axis titles.
# Units are deliberately on a second line to improve readability.
Y_LABELS = [
    "Chamber CO₂ concentration\n(ppm)",
    "MFC CO₂ flow\n(mLn min⁻¹)",
    "Chamber CO₂ change rate\n(ppm s⁻¹)",
    "Corrected net CO₂ exchange\n(µmol CO₂ s⁻¹)",
    "Average-supply A\n(µmol CO₂ s⁻¹)",
    "MFC-only A reference\n(µmol CO₂ s⁻¹)",
]

SECONDARY_Y_LABEL_PANEL_2 = "Average CO₂ supply\n(µmol CO₂ s⁻¹)"


# ============================================================
# Utility functions
# ============================================================

def find_latest_csv(folder: Path) -> Path | None:
    csv_files = list(folder.glob("*.csv"))
    if not csv_files:
        return None
    return max(csv_files, key=lambda p: p.stat().st_mtime)


def read_log_csv(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    if TIME_COL in df.columns:
        df["plot_time"] = pd.to_datetime(df[TIME_COL], errors="coerce")
    else:
        df["plot_time"] = pd.NaT

    # Fallback if timestamp is missing or invalid.
    if df["plot_time"].isna().all():
        if ELAPSED_COL in df.columns:
            elapsed = pd.to_numeric(df[ELAPSED_COL], errors="coerce")
            if elapsed.notna().any():
                max_elapsed = elapsed.max()
                now = pd.Timestamp.now()
                df["plot_time"] = now - pd.to_timedelta(max_elapsed - elapsed, unit="s")

    for col in ALL_DATA_COLS + [ELAPSED_COL]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Derive NE for older logs if the controller has not logged it yet.
    if "NE_regression_umol_s" not in df.columns or df["NE_regression_umol_s"].isna().all():
        if "storage_regression_umol_s" in df.columns and "co2_supply_avg_umol_s" in df.columns:
            df["NE_regression_umol_s"] = (
                df["storage_regression_umol_s"] - df["co2_supply_avg_umol_s"]
            )
        elif "A_regression_umol_s" in df.columns:
            df["NE_regression_umol_s"] = -df["A_regression_umol_s"]

    df = df.dropna(subset=["plot_time"]).copy()
    df = df.sort_values("plot_time")

    return df


def available_cols(df: pd.DataFrame, cols: list[str]) -> list[str]:
    return [c for c in cols if c in df.columns and df[c].notna().any()]


def filter_window(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    if df.empty:
        return df

    latest_time = df["plot_time"].max()

    if mode == "10min":
        start_time = latest_time - pd.Timedelta(minutes=10)
    elif mode == "24h":
        start_time = latest_time - pd.Timedelta(hours=24)
    else:
        start_time = df["plot_time"].min()

    return df[df["plot_time"] >= start_time].copy()


def configure_y_axis(ax, ylabel: str, side: str = "left") -> None:
    """
    Make a y-axis readable and keep the axis title with units.
    side = 'left' for primary axes and 'right' for secondary axes.
    """
    ax.set_ylabel(ylabel, fontsize=9, labelpad=10, linespacing=1.25)

    if side == "right":
        ax.yaxis.set_label_position("right")
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", labelright=True, labelleft=False, labelsize=8, pad=3)
        ax.spines["right"].set_visible(True)
        ax.spines["left"].set_visible(False)
    else:
        ax.yaxis.set_label_position("left")
        ax.yaxis.tick_left()
        ax.tick_params(axis="y", labelleft=True, labelright=False, labelsize=8, pad=3)
        ax.spines["left"].set_visible(True)

    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune=None))
    formatter = ScalarFormatter(useOffset=False)
    formatter.set_scientific(False)
    ax.yaxis.set_major_formatter(formatter)
    ax.margins(y=0.12)


def reset_axis(ax, ylabel: str) -> None:
    ax.clear()
    ax.grid(True, alpha=0.25)
    ax.set_title("")
    ax.set_xlabel("")
    configure_y_axis(ax, ylabel, side="left")


def reset_secondary_axis(ax, ylabel: str) -> None:
    ax.clear()
    ax.grid(False)
    ax.set_title("")
    ax.set_xlabel("")
    configure_y_axis(ax, ylabel, side="right")


def plot_group(ax, df: pd.DataFrame, cols: list[str], zero_line: bool = False) -> bool:
    plotted = False

    for col in available_cols(df, cols):
        valid = df[["plot_time", col]].dropna()
        if valid.empty:
            continue

        ax.plot(
            valid["plot_time"],
            valid[col],
            linewidth=1.3,
            alpha=0.95,
            label=LABELS.get(col, col),
        )
        plotted = True

    if zero_line:
        ax.axhline(0, linewidth=0.8, linestyle="--", alpha=0.45)

    if plotted:
        ax.legend(loc="best", fontsize=8.3, frameon=True)

    return plotted


def format_time_axis(ax, mode: str = "10min") -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    if mode == "10min":
        # Major x-axis tick every 1 minute
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
    elif mode == "24h":
        # Major x-axis tick every 1 hour for 24 h view
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    else:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))

    ax.tick_params(axis="x", labelsize=8)

    for label in ax.get_xticklabels():
        label.set_rotation(0)
        label.set_horizontalalignment("center")


def set_clean_ylim(ax, df: pd.DataFrame, cols: list[str], include_zero: bool = False) -> None:
    values = []

    for col in available_cols(df, cols):
        s = df[col].dropna()
        if not s.empty:
            values.extend(s.tolist())

    if include_zero:
        values.append(0.0)

    if not values:
        return

    y_min = min(values)
    y_max = max(values)

    if y_min == y_max:
        pad = max(abs(y_min) * 0.05, 1.0)
    else:
        pad = (y_max - y_min) * 0.12

    ax.set_ylim(y_min - pad, y_max + pad)


# ============================================================
# Real-time plotter
# ============================================================

class RealtimePlotter:
    def __init__(
        self,
        file_path: Path | None,
        folder: Path | None,
        use_latest: bool,
        interval_s: float,
    ):
        self.file_path = file_path
        self.folder = folder
        self.use_latest = use_latest
        self.interval_s = interval_s
        self.mode = "10min"

        self.current_file: Path | None = None

        self.fig, self.axes = plt.subplots(
            6,
            1,
            figsize=(14.5, 14.5),
            sharex=True,
        )

        # Window title shown in the operating-system title bar
        try:
            manager = self.fig.canvas.manager
            if manager is not None:
                set_title = getattr(manager, "set_window_title", None)
                if callable(set_title):
                    set_title("DREAM CO2 Control and Assimilation")
        except Exception:
            pass

        # Visible title inside the matplotlib figure
        self.fig.suptitle(
            "DREAM CO2 Control and Assimilation",
            fontsize=15,
            fontweight="bold",
            y=0.995,
        )

        # One persistent secondary y-axis for panel 2 only.
        self.ax2_secondary = self.axes[1].twinx()

        self.fig.subplots_adjust(
            top=0.955,
            bottom=0.085,
            left=0.115,
            right=0.86,
            hspace=0.26,
        )

        button_ax = self.fig.add_axes((0.43, 0.02, 0.16, 0.04))
        self.button = Button(button_ax, "View past 24 h")
        self.button.on_clicked(self.toggle_view)

        self.status_text = self.fig.text(
            0.01,
            0.02,
            "",
            fontsize=9,
            va="bottom",
            ha="left",
        )

        self.ani = FuncAnimation(
            self.fig,
            self.update,
            interval=max(1000, int(self.interval_s * 1000)),
            blit=False,
            cache_frame_data=False,
        )

    def resolve_file(self) -> Path | None:
        if self.use_latest:
            if self.folder is None:
                return None
            return find_latest_csv(self.folder)

        return self.file_path

    def toggle_view(self, event=None) -> None:
        if self.mode == "10min":
            self.mode = "24h"
            self.button.label.set_text("View last 10 min")
        else:
            self.mode = "10min"
            self.button.label.set_text("View past 24 h")

        self.update(None)
        self.fig.canvas.draw_idle()

    def update(self, frame) -> list[Artist]:
        path = self.resolve_file()

        if path is None or not path.exists():
            for i, ax in enumerate(self.axes):
                reset_axis(ax, Y_LABELS[i])
            reset_secondary_axis(self.ax2_secondary, SECONDARY_Y_LABEL_PANEL_2)
            self.status_text.set_text("No CSV file found.")
            return []

        self.current_file = path

        df = read_log_csv(path)
        df_view = filter_window(df, self.mode)

        for i, ax in enumerate(self.axes):
            reset_axis(ax, Y_LABELS[i])
        reset_secondary_axis(self.ax2_secondary, SECONDARY_Y_LABEL_PANEL_2)

        if df_view.empty:
            self.status_text.set_text(f"{path.name} | No valid data.")
            return []

        # 1. Chamber CO2
        plot_group(self.axes[0], df_view, PANEL1_CO2_COLS, zero_line=False)
        set_clean_ylim(self.axes[0], df_view, PANEL1_CO2_COLS, include_zero=False)

        # 2. MFC set/actual flow, with average CO2 supply on secondary y-axis
        plot_group(self.axes[1], df_view, PANEL2_MFC_COLS, zero_line=True)
        set_clean_ylim(self.axes[1], df_view, PANEL2_MFC_COLS, include_zero=True)

        for col in available_cols(df_view, PANEL2_SUPPLY_COLS):
            valid = df_view[["plot_time", col]].dropna()
            if valid.empty:
                continue
            self.ax2_secondary.plot(
                valid["plot_time"],
                valid[col],
                linewidth=1.3,
                linestyle="--",
                alpha=0.9,
                label=LABELS.get(col, col),
            )

        set_clean_ylim(self.ax2_secondary, df_view, PANEL2_SUPPLY_COLS, include_zero=True)

        # Combined legend for panel 2.
        lines, labels = self.axes[1].get_legend_handles_labels()
        lines2, labels2 = self.ax2_secondary.get_legend_handles_labels()

        existing_legend = self.axes[1].get_legend()
        if existing_legend is not None:
            existing_legend.remove()

        if lines or lines2:
            self.axes[1].legend(
                lines + lines2,
                labels + labels2,
                loc="upper left",
                fontsize=8.3,
                frameon=True,
            )

        # 3. dC/dt regression + raw/simple dC/dt
        plot_group(self.axes[2], df_view, PANEL3_DCDT_COLS, zero_line=True)
        set_clean_ylim(self.axes[2], df_view, PANEL3_DCDT_COLS, include_zero=True)

        # 4. Regression A and NE
        plot_group(self.axes[3], df_view, PANEL4_A_NE_COLS, zero_line=True)
        set_clean_ylim(self.axes[3], df_view, PANEL4_A_NE_COLS, include_zero=True)

        # 5. Average-supply A
        plot_group(self.axes[4], df_view, PANEL5_A_AVG_COLS, zero_line=True)
        set_clean_ylim(self.axes[4], df_view, PANEL5_A_AVG_COLS, include_zero=True)

        # 6. MFC-only A reference
        plot_group(self.axes[5], df_view, PANEL6_A_MFC_COLS, zero_line=True)
        set_clean_ylim(self.axes[5], df_view, PANEL6_A_MFC_COLS, include_zero=True)

        # Format x-axis as HH:MM.
        format_time_axis(self.axes[-1], self.mode)

        # Same x-range across all panels.
        xmin = df_view["plot_time"].min()
        xmax = df_view["plot_time"].max()
        if pd.notna(xmin) and pd.notna(xmax):
            for ax in self.axes:
                ax.set_xlim(xmin, xmax)
            self.ax2_secondary.set_xlim(xmin, xmax)

        # Re-apply y-axis labels and remove plot titles/x-axis titles.
        for i, ax in enumerate(self.axes):
            ax.set_title("")
            ax.set_xlabel("")
            configure_y_axis(ax, Y_LABELS[i], side="left")

        self.ax2_secondary.set_title("")
        self.ax2_secondary.set_xlabel("")
        configure_y_axis(self.ax2_secondary, SECONDARY_Y_LABEL_PANEL_2, side="right")

        latest_time = df_view["plot_time"].max()
        mode_text = "Last 10 min" if self.mode == "10min" else "Past 24 h"
        self.status_text.set_text(
            f"{mode_text} | {path.name} | latest: {latest_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        return []

    def show(self) -> None:
        plt.show()


# ============================================================
# Command line
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Real-time dynamic plotter for DREAM CO2 feedback CSV logs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Specific CSV log file to monitor.",
    )

    parser.add_argument(
        "--folder",
        type=str,
        default="DREAM_CO2_logs",
        help="Folder containing CSV logs.",
    )

    parser.add_argument(
        "--latest",
        action="store_true",
        help="Automatically monitor the latest CSV file in the folder.",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Plot refresh interval in seconds.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    file_path = Path(args.file) if args.file is not None else None
    folder = Path(args.folder) if args.folder is not None else None

    if file_path is None and not args.latest:
        args.latest = True

    plotter = RealtimePlotter(
        file_path=file_path,
        folder=folder,
        use_latest=args.latest,
        interval_s=args.interval,
    )

    plotter.show()


if __name__ == "__main__":
    main()
