# DREAM_Env_logger_plot.py
# PC logger and live plotter for DREAM QT Py environmental sensors
# Improved live plotting:
# - Uses PC received time for x-axis, so plots update even if a QT Py RTC is wrong
# - Uses HH:MM x-axis labels
# - Filters records by PC time, not device epoch
# - Adds robust y-axis autoscaling with padding
# - Shows latest values in each subplot
# - Keeps the existing DREAMHTTPServer/DREAMDataStore structure

import os
import sys
import time
from datetime import datetime, timedelta

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.widgets import Button

from DREAM_sensor_helper_PC import (
    DREAMDataStore,
    DREAMHTTPServer,
    DEVICE_NAMES,
    safe_float,
)


# ============================================================
# User settings
# ============================================================

PC_SERVER_HOST = "0.0.0.0"
PC_SERVER_PORT = 8080

# ============================================================
# Data storage path
# ============================================================
# Default: save CSV logs in a DREAM_env_logs folder beside this script.
# This avoids accidentally saving to an old Desktop/Chamber_server folder
# or to whichever folder the terminal was opened from.
DEFAULT_SAVE_FOLDER = os.path.join(THIS_DIR, "DREAM_env_logs")

# Optional manual override. Leave as an empty string to use DEFAULT_SAVE_FOLDER.
# Example:
# USER_SAVE_FOLDER = r"C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_env_logs"
USER_SAVE_FOLDER = r""

# Optional Windows environment-variable override. This is useful if you do
# not want to edit the script when moving the project folder.
SAVE_FOLDER = (
    os.environ.get("DREAM_ENV_LOG_DIR")
    or USER_SAVE_FOLDER
    or DEFAULT_SAVE_FOLDER
)
SAVE_FOLDER = os.path.abspath(os.path.expanduser(SAVE_FOLDER))

PLOT_REFRESH_S = 2.0

LIVE_WINDOW_S = 5 * 60
PAST_24H_WINDOW_S = 24 * 3600

DEFAULT_VIEW = "5min"     # "5min" or "24h"

# Use PC receive time for plotting.
# This is strongly recommended for live monitoring because it still works
# if one QT Py has not synced its RTC yet.
PLOT_TIME_SOURCE = "pc_received_time"   # "pc_received_time" or "device_epoch"

DEVICE_COLOURS = {
    "DREAM_Sensors_1": "tab:blue",
    "DREAM_Sensors_2": "tab:orange",
    "DREAM_Sensors_3": "tab:green",
    "DREAM_Sensors_4": "tab:red",
    "DREAM_BotFan_1": "tab:purple",
}

# Latest-value text precision in each subplot.
# Stored CSV precision is controlled in DREAM_sensor_helper_PC.py.
VALUE_DISPLAY_FORMATS = {
    "temp_c": ".2f",
    "rh_percent": ".2f",
    "vpd_kpa": ".3f",
    "air_velocity_ms": ".3f",
}


# ============================================================
# Plot configuration
# ============================================================

PLOT_VARIABLES = [
    {
        "key": "rh_percent",
        "title": "RH",
        "ylabel": "RH (%)",
    },
    {
        "key": "temp_c",
        "title": "Temp",
        "ylabel": "Temp (°C)",
    },
    {
        "key": "co2_ppm",
        "title": "CO2",
        "ylabel": "CO2 (ppm)",
    },
    {
        "key": "pressure_hpa",
        "title": "Pressure",
        "ylabel": "Pressure (hPa)",
    },
    {
        "key": "vpd_kpa",
        "title": "Air VPD",
        "ylabel": "Air VPD (kPa)",
    },
    {
        "key": "air_velocity_ms",
        "title": "Air velocity",
        "ylabel": "Air velocity (m s$^{-1}$)",
    },
    {
        "key": "par_umol_m2_s",
        "title": "PAR",
        "ylabel": "PAR (µmol m$^{-2}$ s$^{-1}$)",
    },
    {
        "key": "fan_speed_percent",
        "title": "Fan speed",
        "ylabel": "Fan speed (%)",
    },
    {
        "key": "fan_rpm",
        "title": "Fan RPM",
        "ylabel": "Fan RPM",
    },
]


# ============================================================
# Helper functions
# ============================================================

def parse_datetime_string(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass

    return None


def row_pc_datetime(row):
    return parse_datetime_string(row.get("pc_received_time"))


def row_device_datetime(row):
    ep = safe_float(row.get("epoch"))

    if ep is not None:
        try:
            return datetime.fromtimestamp(ep)
        except Exception:
            pass

    return parse_datetime_string(row.get("timestamp"))


def row_plot_datetime(row):
    if PLOT_TIME_SOURCE == "device_epoch":
        return row_device_datetime(row)

    return row_pc_datetime(row)


def get_records_since_pc_time(data_store, start_dt):
    """Filter records using PC receive time.

    DREAMDataStore.get_records_since() filters by device epoch. That is not
    ideal for live plots if a QT Py RTC is not synced. This function reads
    the in-memory records safely and filters by pc_received_time instead.
    """
    with data_store.lock:
        result = []

        for row in data_store.records:
            dt = row_pc_datetime(row)

            if dt is not None and dt >= start_dt:
                result.append(dict(row))

        return result


def format_latest_value(variable_key, value):
    fmt = VALUE_DISPLAY_FORMATS.get(variable_key)

    if fmt is None:
        return f"{value:g}"

    return format(value, fmt)


def latest_value_text(records, variable_key):
    latest_by_device = {}

    for row in records:
        dev = row.get("device")
        value = safe_float(row.get(variable_key))
        dt = row_plot_datetime(row)

        if dev is None or value is None or dt is None:
            continue

        old = latest_by_device.get(dev)

        if old is None or dt > old[0]:
            latest_by_device[dev] = (dt, value)

    parts = []

    for dev in DEVICE_NAMES:
        item = latest_by_device.get(dev)

        if item is None:
            continue

        parts.append(f"{dev[-1]}={format_latest_value(variable_key, item[1])}")

    return "   ".join(parts)


def set_robust_ylim(ax, y_values):
    if not y_values:
        return

    y_min = min(y_values)
    y_max = max(y_values)

    if y_min == y_max:
        if y_min == 0:
            pad = 1.0
        else:
            pad = abs(y_min) * 0.05
        ax.set_ylim(y_min - pad, y_max + pad)
        return

    span = y_max - y_min
    pad = span * 0.12
    ax.set_ylim(y_min - pad, y_max + pad)


# ============================================================
# Plot dashboard
# ============================================================

class DREAMPlotDashboard:
    def __init__(self, data_store):
        self.data_store = data_store
        self.view_mode = DEFAULT_VIEW

        self.fig, self.axes = plt.subplots(
            5,
            2,
            figsize=(15, 12),
            sharex=False,
        )

        self.axes_flat = self.axes.flatten()

        try:
            if self.fig.canvas.manager is not None:
                self.fig.canvas.manager.set_window_title(
                    "DREAM Environmental Monitoring"
                )
        except Exception:
            pass

        self.fig.subplots_adjust(
            left=0.07,
            right=0.98,
            bottom=0.12,
            top=0.90,
            hspace=0.55,
            wspace=0.25,
        )

        self.ax_button_5min = self.fig.add_axes((0.72, 0.03, 0.11, 0.045))
        self.ax_button_24h = self.fig.add_axes((0.85, 0.03, 0.11, 0.045))

        self.button_5min = Button(self.ax_button_5min, "Live 5 min")
        self.button_24h = Button(self.ax_button_24h, "Past 24 h")

        self.button_5min.on_clicked(self.set_5min)
        self.button_24h.on_clicked(self.set_24h)

    def set_5min(self, event=None):
        self.view_mode = "5min"
        self.update(force=True)

    def set_24h(self, event=None):
        self.view_mode = "24h"
        self.update(force=True)

    def _get_time_window(self):
        end_dt = datetime.now()

        if self.view_mode == "24h":
            start_dt = end_dt - timedelta(seconds=PAST_24H_WINDOW_S)
            title_suffix = "past 24 h"
        else:
            start_dt = end_dt - timedelta(seconds=LIVE_WINDOW_S)
            title_suffix = "live 5 min"

        return start_dt, end_dt, title_suffix

    def _get_records_for_window(self, start_dt):
        if PLOT_TIME_SOURCE == "device_epoch":
            start_epoch = start_dt.timestamp()
            return self.data_store.get_records_since(start_epoch)

        return get_records_since_pc_time(self.data_store, start_dt)

    def _plot_one_variable(self, ax, records, variable, start_dt, end_dt):
        key = variable["key"]
        title = variable["title"]
        ylabel = variable["ylabel"]

        all_y_values = []

        for dev in DEVICE_NAMES:
            x_values = []
            y_values = []

            for row in records:
                if row.get("device") != dev:
                    continue

                x = row_plot_datetime(row)
                y = safe_float(row.get(key))

                if x is None or y is None:
                    continue

                if x < start_dt or x > end_dt:
                    continue

                x_values.append(x)
                y_values.append(y)

            if x_values:
                xy = sorted(zip(x_values, y_values), key=lambda p: p[0])
                x_values = [p[0] for p in xy]
                y_values = [p[1] for p in xy]

                all_y_values.extend(y_values)

                ax.plot(
                    x_values,
                    y_values,
                    linewidth=1.4,
                    marker="o",
                    markersize=2.5,
                    color=DEVICE_COLOURS.get(dev, None),
                    label=dev,
                )

        ax.set_title(title, fontsize=11)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Time of day")
        ax.grid(True, alpha=0.25)

        ax.set_xlim(start_dt, end_dt)
        set_robust_ylim(ax, all_y_values)

        try:
            ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        except Exception:
            pass

        if self.view_mode == "24h":
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        else:
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

        latest_text = latest_value_text(records, key)
        if latest_text:
            ax.text(
                0.01,
                0.97,
                latest_text,
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=8,
                bbox={
                    "boxstyle": "round,pad=0.25",
                    "facecolor": "white",
                    "edgecolor": "0.7",
                    "alpha": 0.8,
                },
            )
        else:
            ax.text(
                0.5,
                0.5,
                "No valid data",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                alpha=0.6,
            )

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(
                fontsize=8,
                loc="upper right",
                frameon=True,
            )

    def update(self, force=False):
        start_dt, end_dt, title_suffix = self._get_time_window()
        records = self._get_records_for_window(start_dt)

        for ax in self.axes_flat:
            ax.clear()

        for i, variable in enumerate(PLOT_VARIABLES):
            ax = self.axes_flat[i]
            self._plot_one_variable(
                ax=ax,
                records=records,
                variable=variable,
                start_dt=start_dt,
                end_dt=end_dt,
            )

        for j in range(len(PLOT_VARIABLES), len(self.axes_flat)):
            self.axes_flat[j].axis("off")

        time_source_label = "PC received time" if PLOT_TIME_SOURCE == "pc_received_time" else "QT Py device time"

        self.fig.suptitle(
            f"DREAM Environmental Monitoring — {title_suffix} ({time_source_label})",
            fontsize=16,
        )

        if not records:
            self.axes_flat[0].text(
                0.5,
                0.2,
                "No records in selected time window",
                transform=self.axes_flat[0].transAxes,
                ha="center",
                va="center",
                fontsize=10,
                alpha=0.7,
            )

        for ax in self.axes_flat:
            for label in ax.get_xticklabels():
                label.set_rotation(30)
                label.set_ha("right")

        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()




def verify_save_folder(folder):
    """Create the log folder and check that Python can write to it."""
    folder = os.path.abspath(os.path.expanduser(str(folder)))
    os.makedirs(folder, exist_ok=True)

    test_path = os.path.join(folder, "._dream_write_test.tmp")
    with open(test_path, "w", encoding="utf-8") as f:
        f.write("DREAM write test\n")
    os.remove(test_path)

    return folder


# ============================================================
# Main program
# ============================================================

def main():
    verified_save_folder = verify_save_folder(SAVE_FOLDER)
    data_store = DREAMDataStore(verified_save_folder)

    server = DREAMHTTPServer(
        host=PC_SERVER_HOST,
        port=PC_SERVER_PORT,
        data_store=data_store,
    )

    server.start_in_thread()

    print()
    print("=" * 100)
    print("DREAM PC logger and plotter started")
    print(f"Listening on: {PC_SERVER_HOST}:{PC_SERVER_PORT}")
    print(f"Script folder: {THIS_DIR}")
    print(f"Save folder: {data_store.save_folder}")
    print(f"CSV file today: {data_store.csv_path()}")
    print(f"Plot time source: {PLOT_TIME_SOURCE}")
    print()
    print("Set this in every QT Py code.py:")
    print(f'PC_PORT = {PC_SERVER_PORT}')
    print()
    print("Find your PC IPv4 address using ipconfig, then set for example:")
    print('PC_IP = "192.168.137.1"')
    print("=" * 100)
    print()

    dashboard = DREAMPlotDashboard(data_store)

    last_update = 0

    try:
        while True:
            now = time.time()

            if now - last_update >= PLOT_REFRESH_S:
                last_update = now
                dashboard.update()
                print(data_store.summary_text())

            plt.pause(0.2)

    except KeyboardInterrupt:
        print()
        print("Stopping DREAM logger and plotter...")

    finally:
        server.stop()


if __name__ == "__main__":
    main()

