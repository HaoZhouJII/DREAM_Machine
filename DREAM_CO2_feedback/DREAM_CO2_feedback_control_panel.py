#!/usr/bin/env python3
"""
DREAM_CO2_feedback_control_panel.py

Control panel for DREAM_CO2_feedback_controller.py.

Purpose
-------
Use this panel to change CO2 feedback, feedforward and assimilation-calculation
parameters without editing the controller script.

How it works
------------
1. The panel writes parameter values to:
       DREAM_CO2_feedback_settings.json

2. The controller reads this JSON file at startup and then reloads
   live-tunable parameters while running.

3. The panel can also launch the real-time plotter:
       DREAM_CO2_feedback_realtime_plotter.py

Important
---------
Live-tunable parameters update while the controller is running.
Startup-only parameters require restarting the controller:
    - serial ports
    - baud rates
    - MFC full scale
    - log folder / prefix
    - dry-run / precheck flags

Run
---
    python DREAM_CO2_feedback_control_panel.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


CONTROLLER_SCRIPT = "DREAM_CO2_feedback_controller.py"
PLOTTER_SCRIPT = "DREAM_CO2_feedback_realtime_plotter.py"
CONFIG_FILE = "DREAM_CO2_feedback_settings.json"


PARAM_GROUPS = {
    "Manual MFC override": [
        ("MANUAL_MFC_OVERRIDE_ENABLED", "Enable manual MFC override", "live_bool"),
        ("MANUAL_MFC_FLOW_MLN_MIN", "Manual MFC flow (mLn min-1)", "live"),
    ],
    "Feedback control": [
        ("TARGET_CO2_PPM", "Target CO2 (ppm)", "live"),
        ("DEADBAND_PPM", "Deadband (ppm)", "live"),
        ("KP_MLN_MIN_PER_PPM", "KP: feedback gain (mLn min-1 ppm-1)", "live"),
        ("KI_MLN_MIN_PER_PPM_S", "KI: integral gain", "live"),
        ("MAX_CO2_FLOW_MLN_MIN", "Max CO2 flow (mLn min-1)", "live"),
        ("HIGH_CO2_CUTOFF_PPM", "High CO2 cutoff (ppm)", "live"),
        ("CONTROL_INTERVAL_S", "Control interval (s)", "live"),
        ("CO2_AVERAGE_WINDOW_S", "CO2 average window for feedback (s)", "live"),
    ],
    "MFC stability": [
        ("MAX_MFC_STEP_MLN_MIN", "Max MFC step per update (mLn min-1)", "live"),
        ("MIN_MFC_ON_TIME_S", "Minimum MFC ON time (s)", "live"),
        ("MIN_MFC_OFF_TIME_S", "Minimum MFC OFF time (s)", "live"),
        ("MIN_EFFECTIVE_MFC_FLOW_MLN_MIN", "Minimum effective MFC flow (mLn min-1)", "live"),
        ("MFC_TO_LI850_LAG_S", "MFC-to-LI850 lag (s)", "live"),
    ],
    "Feedforward": [
        ("USE_FEEDFORWARD_CONTROL", "Use feedforward control", "live_bool"),
        ("FEEDFORWARD_A_WINDOW_S", "Feedforward A window (s)", "live"),
        ("MAX_FEEDFORWARD_FLOW_MLN_MIN", "Max feedforward flow (mLn min-1)", "live"),
        ("FEEDFORWARD_SMOOTHING_ALPHA", "Feedforward smoothing alpha", "live"),
        ("FEEDFORWARD_WITHIN_DEADBAND_FACTOR", "Feedforward within-deadband factor", "live"),
    ],
    "Assimilation calculation": [
        ("MFC_SUPPLY_AVERAGE_WINDOW_S", "MFC supply average window (s)", "live"),
        ("A_SMOOTHING_WINDOW_S", "A smoothing window (s)", "live"),
        ("DCDT_WINDOW_S", "Simple dC/dt window (s)", "live"),
        ("USE_REGRESSION_DCDT", "Use regression dC/dt", "live_bool"),
        ("REGRESSION_DCDT_WINDOW_S", "Regression dC/dt window (s)", "live"),
        ("REGRESSION_DCDT_SHORT_WINDOW_S", "Short regression dC/dt window (s)", "live"),
        ("A_REGRESSION_SMOOTHING_WINDOW_S", "A regression smoothing window (s)", "live"),
    ],
    "Physical / gas settings": [
        ("CO2_FRACTION_IN_MFC_GAS", "CO2 fraction in MFC gas", "live"),
        ("CHAMBER_VOLUME_M3", "Chamber volume (m3)", "live"),
        ("CHAMBER_AIR_TEMP_C", "Chamber air temperature (deg C)", "live"),
        ("LEAF_AREA_M2", "Leaf area (m2, blank=None)", "live_optional"),
    ],
    "Startup / hardware settings": [
        ("LI850_PORT", "LI-850 COM port", "restart"),
        ("BRONKHORST_PORT", "Bronkhorst COM port", "restart"),
        ("BRONKHORST_ADDRESS", "Bronkhorst FLOW-BUS address", "restart"),
        ("BRONKHORST_BAUDRATE", "Bronkhorst baudrate", "restart"),
        ("MFC_FULL_SCALE_MLN_MIN", "MFC full scale (mLn min-1)", "restart"),
        ("LI850_BAUDRATE", "LI-850 baudrate", "restart"),
        ("LI850_TIMEOUT_S", "LI-850 timeout (s)", "restart"),
        ("LOG_FOLDER", "Log folder", "restart"),
        ("LOG_PREFIX", "Log prefix", "restart"),
    ],
    "Safety / precheck": [
        ("SET_MFC_ZERO_ON_EXIT", "Set MFC zero on exit", "live_bool"),
        ("STOP_IF_NO_LI850_DATA_FOR_S", "Stop if no LI-850 data for (s)", "live"),
        ("DRY_RUN_NO_MFC_WRITE", "Dry run, no MFC write", "restart_bool"),
        ("RUN_PRECHECK_BEFORE_CONTROL", "Run precheck before control", "restart_bool"),
        ("CHECK_MFC_CONNECTION_BEFORE_CONTROL", "Check MFC before control", "restart_bool"),
        ("RUN_SMALL_MFC_SETPOINT_TEST", "Run small MFC setpoint test", "restart_bool"),
        ("MFC_TEST_FLOW_MLN_MIN", "MFC test flow (mLn min-1)", "restart"),
        ("MFC_TEST_HOLD_S", "MFC test hold (s)", "restart"),
        ("MFC_ZERO_TOLERANCE_MLN_MIN", "MFC zero tolerance (mLn min-1)", "restart"),
    ],
}

DEFAULT_CONFIG = {
    "LI850_PORT": "COM11",
    "BRONKHORST_PORT": "COM5",
    "BRONKHORST_ADDRESS": 6,
    "BRONKHORST_BAUDRATE": 38400,
    "MFC_FULL_SCALE_MLN_MIN": 200.0,
    "LI850_BAUDRATE": 9600,
    "LI850_TIMEOUT_S": 1.0,
    "LOG_FOLDER": "DREAM_CO2_logs",
    "LOG_PREFIX": "DREAM_CO2_feedback_v2_refined",

    "CO2_FRACTION_IN_MFC_GAS": 1.0,
    "CHAMBER_VOLUME_M3": 2.33,
    "CHAMBER_AIR_TEMP_C": 18.0,
    "LEAF_AREA_M2": None,

    "TARGET_CO2_PPM": 452.0,
    "DEADBAND_PPM": 2.0,
    "KP_MLN_MIN_PER_PPM": 1.0,
    "KI_MLN_MIN_PER_PPM_S": 0.0,
    "MAX_CO2_FLOW_MLN_MIN": 50.0,
    "HIGH_CO2_CUTOFF_PPM": 457.0,
    "CONTROL_INTERVAL_S": 5.0,
    "CO2_AVERAGE_WINDOW_S": 20.0,

    "MAX_MFC_STEP_MLN_MIN": 3.0,
    "MIN_MFC_ON_TIME_S": 20.0,
    "MIN_MFC_OFF_TIME_S": 20.0,
    "MIN_EFFECTIVE_MFC_FLOW_MLN_MIN": 0.5,
    "MFC_TO_LI850_LAG_S": 10.0,

    "USE_FEEDFORWARD_CONTROL": True,
    "FEEDFORWARD_A_WINDOW_S": 180.0,
    "MAX_FEEDFORWARD_FLOW_MLN_MIN": 15.0,
    "FEEDFORWARD_SMOOTHING_ALPHA": 0.2,
    "FEEDFORWARD_WITHIN_DEADBAND_FACTOR": 0.3,

    "MFC_SUPPLY_AVERAGE_WINDOW_S": 180.0,
    "A_SMOOTHING_WINDOW_S": 180.0,
    "DCDT_WINDOW_S": 60.0,
    "USE_REGRESSION_DCDT": True,
    "REGRESSION_DCDT_WINDOW_S": 180.0,
    "REGRESSION_DCDT_SHORT_WINDOW_S": 60.0,
    "A_REGRESSION_SMOOTHING_WINDOW_S": 180.0,

    "SET_MFC_ZERO_ON_EXIT": True,
    "STOP_IF_NO_LI850_DATA_FOR_S": 30.0,
    "DRY_RUN_NO_MFC_WRITE": False,
    "RUN_PRECHECK_BEFORE_CONTROL": False,
    "CHECK_MFC_CONNECTION_BEFORE_CONTROL": False,
    "RUN_SMALL_MFC_SETPOINT_TEST": True,
    "MFC_TEST_FLOW_MLN_MIN": 5.0,
    "MFC_TEST_HOLD_S": 3.0,
    "MFC_ZERO_TOLERANCE_MLN_MIN": 0.5,
}


class ControlPanel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DREAM CO2 Feedback Control Panel")
        self.root.geometry("1050x760")

        self.base_dir = Path(__file__).resolve().parent
        self.controller_path = self.base_dir / CONTROLLER_SCRIPT
        self.plotter_path = self.base_dir / PLOTTER_SCRIPT
        self.config_path = self.base_dir / CONFIG_FILE

        self.controller_proc: subprocess.Popen | None = None
        self.plotter_proc: subprocess.Popen | None = None

        self.vars: dict[str, tk.StringVar | tk.BooleanVar] = {}
        self.plot_interval_var = tk.StringVar(value="5")
        self.status_var = tk.StringVar(value="Ready.")
        self.python_var = tk.StringVar(value=sys.executable)

        self.ensure_config_exists()
        self.config = self.load_config()

        self.build_ui()
        self.load_values_into_widgets()

    # ------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------
    def ensure_config_exists(self):
        if not self.config_path.exists():
            self.config_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")

    def load_config(self) -> dict:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        merged = DEFAULT_CONFIG.copy()
        merged.update(data)
        return merged

    def save_config(self):
        data = self.collect_values_from_widgets()
        self.config = data
        self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.status_var.set(
            "Saved settings to JSON. Live-tunable parameters will be reloaded by the running controller within ~2 s."
        )

    def collect_values_from_widgets(self) -> dict:
        data = {}

        for group_items in PARAM_GROUPS.values():
            for key, label, kind in group_items:
                var = self.vars[key]

                if kind.endswith("_bool"):
                    data[key] = bool(var.get())
                    continue

                raw = str(var.get()).strip()

                if kind == "live_optional" and raw == "":
                    data[key] = None
                    continue

                default = DEFAULT_CONFIG.get(key)

                if isinstance(default, int) and not isinstance(default, bool):
                    try:
                        data[key] = int(float(raw))
                    except ValueError:
                        data[key] = default
                elif isinstance(default, float) or default is None:
                    try:
                        data[key] = float(raw)
                    except ValueError:
                        data[key] = None if default is None else default
                else:
                    data[key] = raw

        return data

    def load_values_into_widgets(self):
        for group_items in PARAM_GROUPS.values():
            for key, label, kind in group_items:
                value = self.config.get(key, DEFAULT_CONFIG.get(key))

                if kind.endswith("_bool"):
                    self.vars[key].set(bool(value))
                else:
                    self.vars[key].set("" if value is None else str(value))

    def reload_from_json(self):
        self.config = self.load_config()
        self.load_values_into_widgets()
        self.status_var.set("Reloaded values from JSON.")

    def reset_defaults(self):
        if not messagebox.askyesno("Reset defaults", "Reset all settings to default values?"):
            return
        self.config = DEFAULT_CONFIG.copy()
        self.config_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        self.load_values_into_widgets()
        self.status_var.set("Reset settings to defaults and saved JSON.")

    # ------------------------------------------------------------
    # UI
    # ------------------------------------------------------------
    def build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=12, pady=8)

        title = ttk.Label(top, text="DREAM CO2 Feedback Control Panel", font=("Segoe UI", 15, "bold"))
        title.grid(row=0, column=0, sticky="w")

        ttk.Label(top, text="Python:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.python_var, width=95).grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(top, text=f"Config: {self.config_path}").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(top, text=f"Controller: {self.controller_path.name}").grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Label(top, text=f"Plotter: {self.plotter_path.name}").grid(row=4, column=0, columnspan=3, sticky="w")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=12, pady=8)

        for group_name, items in PARAM_GROUPS.items():
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=group_name)
            self.populate_group(frame, items)

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", padx=12, pady=6)

        ttk.Button(controls, text="Save / Apply live settings", command=self.save_config).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(controls, text="Reload from JSON", command=self.reload_from_json).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(controls, text="Reset defaults", command=self.reset_defaults).grid(row=0, column=2, padx=4, pady=4)

        ttk.Separator(controls, orient="vertical").grid(row=0, column=3, sticky="ns", padx=10)

        ttk.Button(controls, text="Start controller", command=self.start_controller).grid(row=0, column=4, padx=4, pady=4)
        ttk.Button(controls, text="Stop controller", command=self.stop_controller).grid(row=0, column=5, padx=4, pady=4)

        ttk.Separator(controls, orient="vertical").grid(row=0, column=6, sticky="ns", padx=10)

        ttk.Label(controls, text="Plot refresh (s):").grid(row=0, column=7, padx=4, pady=4)
        ttk.Entry(controls, textvariable=self.plot_interval_var, width=7).grid(row=0, column=8, padx=4, pady=4)
        ttk.Button(controls, text="Start real-time plot", command=self.start_plotter).grid(row=0, column=9, padx=4, pady=4)
        ttk.Button(controls, text="Stop plotter", command=self.stop_plotter).grid(row=0, column=10, padx=4, pady=4)

        ttk.Button(controls, text="Open log folder", command=self.open_log_folder).grid(row=0, column=11, padx=4, pady=4)

        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w", wraplength=1000)
        status.pack(fill="x", padx=12, pady=(0, 8))

    def populate_group(self, frame: ttk.Frame, items: list[tuple[str, str, str]]):
        explanation = ttk.Label(
            frame,
            text="live = updates while controller is running; restart = requires restarting the controller",
            foreground="#555555",
        )
        explanation.grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 10))

        for row, (key, label, kind) in enumerate(items, start=1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=5)
            ttk.Label(frame, text=key, foreground="#666666").grid(row=row, column=1, sticky="w", padx=10, pady=5)

            if kind.endswith("_bool"):
                var = tk.BooleanVar()
                widget = ttk.Checkbutton(frame, variable=var)
            else:
                var = tk.StringVar()
                widget = ttk.Entry(frame, textvariable=var, width=24)

            self.vars[key] = var
            widget.grid(row=row, column=2, sticky="w", padx=10, pady=5)

            status = "live" if kind.startswith("live") else "restart"
            colour = "#1b5e20" if status == "live" else "#b26a00"
            ttk.Label(frame, text=status, foreground=colour).grid(row=row, column=3, sticky="w", padx=10, pady=5)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

    # ------------------------------------------------------------
    # Process launch / stop
    # ------------------------------------------------------------
    def build_controller_command(self) -> list[str] | None:
        if not self.controller_path.exists():
            messagebox.showerror("Missing controller", f"Cannot find:\n{self.controller_path}")
            return None

        return [
            self.python_var.get(),
            str(self.controller_path),
            "--config",
            str(self.config_path),
        ]

    def start_controller(self):
        self.save_config()

        cmd = self.build_controller_command()
        if cmd is None:
            return

        if self.controller_proc is not None and self.controller_proc.poll() is None:
            if not messagebox.askyesno("Controller already running", "A controller launched by this panel is already running. Start another one?"):
                return

        try:
            self.controller_proc = subprocess.Popen(cmd, cwd=str(self.base_dir))
            self.status_var.set("Started controller. Live settings will reload from JSON automatically.")
        except Exception as exc:
            messagebox.showerror("Failed to start controller", str(exc))

    def stop_controller(self):
        if self.controller_proc is None or self.controller_proc.poll() is not None:
            self.status_var.set("No running controller process launched by this panel.")
            return
        if not messagebox.askyesno("Stop controller", "Stop the controller process launched by this panel?"):
            return
        try:
            self.controller_proc.terminate()
            self.status_var.set("Stopped controller process. The controller should set MFC to zero on exit if enabled.")
        except Exception as exc:
            messagebox.showerror("Failed to stop controller", str(exc))

    def start_plotter(self):
        self.save_config()

        if not self.plotter_path.exists():
            messagebox.showerror("Missing plotter", f"Cannot find:\n{self.plotter_path}")
            return

        try:
            interval = float(self.plot_interval_var.get())
        except ValueError:
            messagebox.showerror("Invalid interval", "Plot refresh interval must be a number.")
            return

        log_folder = self.base_dir / str(self.config.get("LOG_FOLDER", "DREAM_CO2_logs"))

        cmd = [
            self.python_var.get(),
            str(self.plotter_path),
            "--folder",
            str(log_folder),
            "--latest",
            "--interval",
            str(interval),
        ]

        try:
            self.plotter_proc = subprocess.Popen(cmd, cwd=str(self.base_dir))
            self.status_var.set("Started real-time plotter using latest CSV in log folder.")
        except Exception as exc:
            messagebox.showerror("Failed to start plotter", str(exc))

    def stop_plotter(self):
        if self.plotter_proc is None or self.plotter_proc.poll() is not None:
            self.status_var.set("No running plotter process launched by this panel.")
            return
        try:
            self.plotter_proc.terminate()
            self.status_var.set("Stopped plotter process.")
        except Exception as exc:
            messagebox.showerror("Failed to stop plotter", str(exc))

    def open_log_folder(self):
        log_folder = self.base_dir / str(self.collect_values_from_widgets().get("LOG_FOLDER", "DREAM_CO2_logs"))
        log_folder.mkdir(parents=True, exist_ok=True)

        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(log_folder)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_folder)])
            else:
                subprocess.Popen(["xdg-open", str(log_folder)])
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc))

    def on_close(self):
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ControlPanel(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
