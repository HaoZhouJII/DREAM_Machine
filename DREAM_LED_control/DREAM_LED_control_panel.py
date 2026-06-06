# DREAM_LED_control_panel.py
# GUI control panel for DREAM LED RP2040 controllers
#
# Put this file in:
#   C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_LED_control
#
# Run with:
#   py DREAM_LED_control_panel.py
# or use Start_DREAM_LED_Control_Panel.bat
#
# Requirements:
#   py -m pip install pyserial
#
# This GUI uses DREAM_LED_PC.py in the same folder.
#
# Features:
#   1. Manual COM connection and auto scan
#   2. Per-device, per-DAC, per-channel LED control
#   3. DAC-level Set all
#   4. Device-level Set All To
#   5. Fixed constant lighting regime
#   6. PC-driven diurnal lighting regime
#      - independent diurnal ON/OFF time
#      - automatic peak time = midpoint between diurnal ON/OFF
#      - curve power 0-5
#      - separate white/red peak scale percentages

import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

from DREAM_LED_PC import DreamLEDPC


try:
    import run_dream_led as default_config
except Exception:
    default_config = None


APP_TITLE = "DREAM LED Control Panel - Diurnal Regime + Preview"
CONFIG_FILE = "DREAM_LED_control_panel_config.json"

DEFAULT_EXPECTED_DEVICES = [
    "WHITE_LED_RP2040_1",
    "WHITE_LED_RP2040_2",
    "RED_LED_RP2040",
    "UVIR_LED_RP2040_1",
    "UVIR_LED_RP2040_2",
]

DEFAULT_MANUAL_PORTS = {
    "WHITE_LED_RP2040_1": "COM7",
    "WHITE_LED_RP2040_2": "COM8",
    "RED_LED_RP2040": "COM16",
    "UVIR_LED_RP2040_1": "",
    "UVIR_LED_RP2040_2": "",
}

DEFAULT_LED_SETTINGS = {
    "WHITE_LED_RP2040_1": {
        "0x60": {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0},
        "0x61": {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0},
        "0x62": {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0},
    },
    "WHITE_LED_RP2040_2": {
        "0x60": {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0},
        "0x61": {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0},
        "0x62": {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0},
    },
    "RED_LED_RP2040": {
        "0x60": {"A": 9.0, "B": 9.0, "C": 9.0, "D": 9.0},
        "0x61": {"A": 9.0, "B": 9.0, "C": 9.0, "D": 9.0},
        "0x62": {"A": 9.0, "B": 0.5, "C": 0.5, "D": 0.0},
    },
    "UVIR_LED_RP2040_1": {
        "0x60": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},
        "0x61": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},
        "0x62": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},
    },
    "UVIR_LED_RP2040_2": {
        "0x60": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},
        "0x61": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},
        "0x62": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},
    },
}

DAC_ADDRS = ["0x60", "0x61", "0x62"]
CHANNELS = ["A", "B", "C", "D"]


def deep_copy(obj):
    return json.loads(json.dumps(obj))


def load_defaults_from_run_file():
    expected = deep_copy(DEFAULT_EXPECTED_DEVICES)
    ports = deep_copy(DEFAULT_MANUAL_PORTS)
    settings = deep_copy(DEFAULT_LED_SETTINGS)

    if default_config is not None:
        expected = list(getattr(default_config, "EXPECTED_DEVICES", expected))
        ports.update(dict(getattr(default_config, "MANUAL_PORTS", {})))

        settings_from_file = getattr(default_config, "LED_SETTINGS", None)
        if isinstance(settings_from_file, dict):
            for dev, dac_block in settings_from_file.items():
                settings.setdefault(dev, {})
                for dac, channel_block in dac_block.items():
                    settings[dev].setdefault(dac, {})
                    settings[dev][dac].update(channel_block)

    for dev in expected:
        ports.setdefault(dev, "")
        settings.setdefault(dev, {})
        for dac in DAC_ADDRS:
            settings[dev].setdefault(dac, {})
            for ch in CHANNELS:
                settings[dev][dac].setdefault(ch, 0.0)

    return expected, ports, settings


def parse_hhmm_to_minutes(txt):
    parts = str(txt).strip().split(":")
    if len(parts) != 2:
        raise ValueError("Time must be HH:MM")
    h = int(parts[0])
    m = int(parts[1])
    if h < 0 or h > 23:
        raise ValueError("Hour must be 0-23")
    if m < 0 or m > 59:
        raise ValueError("Minute must be 0-59")
    return h * 60 + m


def minutes_to_hhmm(minutes):
    minutes = int(minutes) % (24 * 60)
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


class DreamLEDControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1500x900")
        self.minsize(1200, 760)

        self.expected_devices, self.manual_ports, self.led_settings = load_defaults_from_run_file()

        self.connected_devices = set()
        self.led = None
        self.worker_lock = threading.Lock()
        self.log_queue = queue.Queue()

        self.baudrate_var = tk.IntVar(value=115200)
        self.connect_delay_var = tk.DoubleVar(value=2.0)
        self.command_delay_var = tk.DoubleVar(value=0.20)
        self.read_extra_var = tk.DoubleVar(value=0.20)

        # Fixed regime
        self.light_on_var = tk.StringVar(value="08:00")
        self.light_off_var = tk.StringVar(value="20:00")

        # Diurnal regime
        self.diurnal_on_var = tk.StringVar(value="08:00")
        self.diurnal_off_var = tk.StringVar(value="20:00")
        self.diurnal_peak_var = tk.StringVar(value="14:00")
        self.diurnal_factor_var = tk.StringVar(value="0.000")
        self.diurnal_mode_status_var = tk.StringVar(value="Stopped")
        self.preview_info_var = tk.StringVar(value="Preview: factor curve")
        self.curve_power_var = tk.DoubleVar(value=1.0)        # 0-5
        self.diurnal_interval_var = tk.IntVar(value=60)
        self.white_peak_scale_var = tk.DoubleVar(value=100.0)
        self.red_peak_scale_var = tk.DoubleVar(value=100.0)
        self.include_uvir_var = tk.BooleanVar(value=False)

        self.heartbeat_var = tk.StringVar(value="ON")
        self.live_update_var = tk.BooleanVar(value=False)
        self.auto_disconnect_var = tk.BooleanVar(value=False)

        self.diurnal_running = False
        self.diurnal_after_id = None

        self.port_vars = {}
        self.connected_label_vars = {}

        self.channel_vars = {}
        self.channel_widgets = {}

        self.device_all_vars = {}
        self.dac_all_vars = {}

        self._load_gui_config_if_available()
        self._update_auto_peak()
        self._build_ui()
        self._bind_diurnal_preview_updates()
        self._draw_diurnal_preview()
        self._poll_log_queue()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ======================================================
    # Config persistence
    # ======================================================

    def _load_gui_config_if_available(self):
        if not os.path.exists(CONFIG_FILE):
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            self.manual_ports.update(cfg.get("manual_ports", {}))

            cfg_settings = cfg.get("led_settings", {})
            for dev, dac_block in cfg_settings.items():
                self.led_settings.setdefault(dev, {})
                for dac, channel_block in dac_block.items():
                    self.led_settings[dev].setdefault(dac, {})
                    self.led_settings[dev][dac].update(channel_block)

            schedule = cfg.get("schedule", {})
            self.light_on_var.set(schedule.get("on", self.light_on_var.get()))
            self.light_off_var.set(schedule.get("off", self.light_off_var.get()))

            diurnal = cfg.get("diurnal", {})
            self.diurnal_on_var.set(diurnal.get("on", self.diurnal_on_var.get()))
            self.diurnal_off_var.set(diurnal.get("off", self.diurnal_off_var.get()))
            self.curve_power_var.set(float(diurnal.get("curve_power", self.curve_power_var.get())))
            self.diurnal_interval_var.set(int(diurnal.get("interval_s", self.diurnal_interval_var.get())))
            self.white_peak_scale_var.set(float(diurnal.get("white_peak_scale", self.white_peak_scale_var.get())))
            self.red_peak_scale_var.set(float(diurnal.get("red_peak_scale", self.red_peak_scale_var.get())))
            self.include_uvir_var.set(bool(diurnal.get("include_uvir", self.include_uvir_var.get())))

        except Exception as e:
            self.log(f"Could not load GUI config: {e}")

    def save_gui_config(self):
        self._sync_vars_to_settings()

        cfg = {
            "manual_ports": self.get_manual_ports_from_ui(),
            "led_settings": self.led_settings,
            "schedule": {
                "on": self.light_on_var.get(),
                "off": self.light_off_var.get(),
            },
            "diurnal": {
                "on": self.diurnal_on_var.get(),
                "off": self.diurnal_off_var.get(),
                "curve_power": float(self.curve_power_var.get()),
                "interval_s": int(self.diurnal_interval_var.get()),
                "white_peak_scale": float(self.white_peak_scale_var.get()),
                "red_peak_scale": float(self.red_peak_scale_var.get()),
                "include_uvir": bool(self.include_uvir_var.get()),
            },
        }

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        self.log(f"Saved GUI config to {CONFIG_FILE}")

    # ======================================================
    # UI construction
    # ======================================================

    def _build_ui(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(
            top,
            text="DREAM LED Control Panel - Diurnal Regime + Preview",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")

        ttk.Button(top, text="Save GUI config", command=self.save_gui_config).pack(side="right", padx=4)
        ttk.Button(top, text="Load preset JSON", command=self.load_preset_json).pack(side="right", padx=4)
        ttk.Button(top, text="Save preset JSON", command=self.save_preset_json).pack(side="right", padx=4)

        main_pane = ttk.PanedWindow(root, orient="horizontal")
        main_pane.pack(fill="both", expand=True)

        left = ttk.Frame(main_pane, width=430)
        right = ttk.Frame(main_pane)

        main_pane.add(left, weight=0)
        main_pane.add(right, weight=1)

        self._build_connection_panel(left)
        self._build_schedule_panel(left)
        self._build_lighting_regime_panel(left)
        self._build_log_panel(left)
        self._build_channel_panel(right)

    def _build_connection_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Connection", padding=8)
        frame.pack(fill="x", pady=(0, 8))

        ttk.Label(frame, text="Baudrate").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.baudrate_var, width=10).grid(row=0, column=1, sticky="w")

        ttk.Label(frame, text="Connect delay s").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.connect_delay_var, width=10).grid(row=1, column=1, sticky="w")

        ttk.Checkbutton(
            frame,
            text="Disconnect after each command",
            variable=self.auto_disconnect_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 4))

        ttk.Separator(frame).grid(row=3, column=0, columnspan=3, sticky="ew", pady=6)

        ttk.Label(frame, text="Device").grid(row=4, column=0, sticky="w")
        ttk.Label(frame, text="COM port").grid(row=4, column=1, sticky="w")
        ttk.Label(frame, text="Status").grid(row=4, column=2, sticky="w")

        for r, dev in enumerate(self.expected_devices, start=5):
            ttk.Label(frame, text=dev).grid(row=r, column=0, sticky="w", pady=2)

            var = tk.StringVar(value=self.manual_ports.get(dev, ""))
            self.port_vars[dev] = var
            ttk.Entry(frame, textvariable=var, width=10).grid(row=r, column=1, sticky="w", padx=4)

            status_var = tk.StringVar(value="Not connected")
            self.connected_label_vars[dev] = status_var
            ttk.Label(frame, textvariable=status_var, width=18).grid(row=r, column=2, sticky="w")

        btns = ttk.Frame(frame)
        btns.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        ttk.Button(btns, text="List COM", command=self.list_ports).pack(side="left", padx=2)
        ttk.Button(btns, text="Connect selected", command=self.connect_selected).pack(side="left", padx=2)
        ttk.Button(btns, text="Auto scan", command=self.auto_scan).pack(side="left", padx=2)
        ttk.Button(btns, text="Disconnect", command=self.disconnect).pack(side="left", padx=2)

        for i in range(3):
            frame.columnconfigure(i, weight=1)

    def _build_schedule_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Fixed constant regime", padding=8)
        frame.pack(fill="x", pady=(0, 8))

        ttk.Label(frame, text="Fixed ON").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.light_on_var, width=8).grid(row=0, column=1, sticky="w")

        ttk.Label(frame, text="Fixed OFF").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Entry(frame, textvariable=self.light_off_var, width=8).grid(row=0, column=3, sticky="w")

        ttk.Button(frame, text="Set PC time", command=self.set_time_all).grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(frame, text="Apply fixed schedule", command=self.apply_fixed_regime).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Button(frame, text="Schedule OFF", command=self.schedule_off_all).grid(row=1, column=3, sticky="ew", pady=4)

        ttk.Button(frame, text="Apply all shown values", command=self.apply_all_channels).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=4
        )
        ttk.Button(frame, text="Status all", command=self.status_all).grid(row=2, column=2, sticky="ew", pady=4)
        ttk.Button(frame, text="I2C scan", command=self.i2c_scan_all).grid(row=2, column=3, sticky="ew", pady=4)

        ttk.Button(frame, text="ON restore targets", command=self.on_all).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=4
        )
        ttk.Button(frame, text="OFF all outputs", command=self.off_all).grid(
            row=3, column=2, columnspan=2, sticky="ew", pady=4
        )

        ttk.Label(frame, text="Heartbeat").grid(row=4, column=0, sticky="w")
        ttk.Combobox(
            frame,
            textvariable=self.heartbeat_var,
            values=["ON", "OFF"],
            width=8,
            state="readonly",
        ).grid(row=4, column=1, sticky="w")

        ttk.Button(frame, text="Apply heartbeat", command=self.apply_heartbeat).grid(
            row=4, column=2, columnspan=2, sticky="ew", pady=4
        )

        ttk.Checkbutton(
            frame,
            text="Live update when sliders move",
            variable=self.live_update_var,
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))

        for i in range(4):
            frame.columnconfigure(i, weight=1)

    def _build_lighting_regime_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Diurnal lighting regime - PC driven", padding=8)
        frame.pack(fill="x", pady=(0, 8))

        # Times
        ttk.Label(frame, text="Diurnal ON").grid(row=0, column=0, sticky="w")
        on_entry = ttk.Entry(frame, textvariable=self.diurnal_on_var, width=8)
        on_entry.grid(row=0, column=1, sticky="w")

        ttk.Label(frame, text="Diurnal OFF").grid(row=0, column=2, sticky="w")
        off_entry = ttk.Entry(frame, textvariable=self.diurnal_off_var, width=8)
        off_entry.grid(row=0, column=3, sticky="w")

        ttk.Label(frame, text="Auto peak").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.diurnal_peak_var, width=8, state="readonly").grid(row=1, column=1, sticky="w")

        ttk.Button(frame, text="Update peak", command=self._update_auto_peak).grid(row=1, column=2, columnspan=2, sticky="ew", pady=2)

        # Curve parameters
        ttk.Label(frame, text="Curve power 0-5").grid(row=2, column=0, sticky="w")
        curve = ttk.Scale(
            frame,
            from_=0,
            to=5,
            variable=self.curve_power_var,
            command=lambda _v: self._on_diurnal_preview_parameter_change(),
        )
        curve.grid(row=2, column=1, columnspan=2, sticky="ew", padx=4)
        ttk.Spinbox(
            frame,
            from_=0,
            to=5,
            increment=0.1,
            textvariable=self.curve_power_var,
            width=8,
            command=self._on_diurnal_preview_parameter_change,
        ).grid(row=2, column=3, sticky="w")

        ttk.Label(frame, text="Interval s").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(frame, from_=5, to=3600, increment=5, textvariable=self.diurnal_interval_var, width=8).grid(row=3, column=1, sticky="w")

        ttk.Label(frame, text="Current factor").grid(row=3, column=2, sticky="w")
        ttk.Entry(frame, textvariable=self.diurnal_factor_var, width=8, state="readonly").grid(row=3, column=3, sticky="w")

        # Peak scales
        ttk.Label(frame, text="White peak scale %").grid(row=4, column=0, sticky="w")
        ttk.Spinbox(
            frame,
            from_=0,
            to=100,
            increment=1,
            textvariable=self.white_peak_scale_var,
            width=8,
            command=self._on_diurnal_preview_parameter_change,
        ).grid(row=4, column=1, sticky="w")

        ttk.Label(frame, text="Red peak scale %").grid(row=4, column=2, sticky="w")
        ttk.Spinbox(
            frame,
            from_=0,
            to=100,
            increment=1,
            textvariable=self.red_peak_scale_var,
            width=8,
            command=self._on_diurnal_preview_parameter_change,
        ).grid(row=4, column=3, sticky="w")

        ttk.Checkbutton(
            frame,
            text="Include UV/IR in diurnal scaling",
            variable=self.include_uvir_var,
            command=self._on_diurnal_preview_parameter_change,
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # Buttons
        ttk.Button(frame, text="Apply diurnal now", command=self.apply_diurnal_now).grid(row=6, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(frame, text="Start diurnal", command=self.start_diurnal).grid(row=6, column=2, sticky="ew", pady=4)
        ttk.Button(frame, text="Stop diurnal", command=self.stop_diurnal).grid(row=6, column=3, sticky="ew", pady=4)

        ttk.Label(frame, text="Mode status").grid(row=7, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.diurnal_mode_status_var, state="readonly").grid(row=7, column=1, columnspan=3, sticky="ew")

        # Preview plot
        preview = ttk.LabelFrame(frame, text="Diurnal light intensity preview", padding=6)
        preview.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        self.preview_canvas = tk.Canvas(preview, width=390, height=185, bg="white", highlightthickness=1, highlightbackground="#cccccc")
        self.preview_canvas.pack(fill="x", expand=True)

        ttk.Label(preview, textvariable=self.preview_info_var).pack(anchor="w", pady=(4, 0))

        # Update peak/plot when time entries change.
        on_entry.bind("<FocusOut>", lambda _e: self._on_diurnal_time_change())
        off_entry.bind("<FocusOut>", lambda _e: self._on_diurnal_time_change())
        on_entry.bind("<Return>", lambda _e: self._on_diurnal_time_change())
        off_entry.bind("<Return>", lambda _e: self._on_diurnal_time_change())

        for i in range(4):
            frame.columnconfigure(i, weight=1)

    # ======================================================
    # Diurnal preview plot
    # ======================================================

    def _bind_diurnal_preview_updates(self):
        # Tk variable traces keep the preview live when values are typed manually.
        for var in [
            self.diurnal_on_var,
            self.diurnal_off_var,
            self.curve_power_var,
            self.white_peak_scale_var,
            self.red_peak_scale_var,
            self.include_uvir_var,
        ]:
            try:
                var.trace_add("write", lambda *_args: self._debounced_draw_diurnal_preview())
            except Exception:
                pass

    def _on_diurnal_time_change(self):
        self._update_auto_peak()
        self._draw_diurnal_preview()

    def _on_diurnal_preview_parameter_change(self):
        self._update_diurnal_factor_label()
        self._draw_diurnal_preview()

    def _debounced_draw_diurnal_preview(self):
        old_after = getattr(self, "_diurnal_preview_after_id", None)
        if old_after is not None:
            try:
                self.after_cancel(old_after)
            except Exception:
                pass
        self._diurnal_preview_after_id = self.after(120, self._draw_diurnal_preview)

    def _diurnal_factor_at_minutes(self, now_min):
        on_min = parse_hhmm_to_minutes(self.diurnal_on_var.get())
        off_min = parse_hhmm_to_minutes(self.diurnal_off_var.get())

        duration = (off_min - on_min) % (24 * 60)
        if duration <= 0:
            return 0.0

        elapsed = (float(now_min) - on_min) % (24 * 60)
        if elapsed < 0 or elapsed > duration:
            return 0.0

        phase = elapsed / duration
        if phase < 0.0 or phase > 1.0:
            return 0.0

        base = math.sin(math.pi * phase)
        if base < 0:
            base = 0.0

        power = max(0.0, min(5.0, float(self.curve_power_var.get())))
        if power == 0.0:
            return 1.0 if 0.0 <= phase <= 1.0 else 0.0

        return max(0.0, min(1.0, base ** power))

    def _draw_diurnal_preview(self):
        if not hasattr(self, "preview_canvas"):
            return

        canvas = self.preview_canvas
        canvas.delete("all")

        width = max(360, int(canvas.winfo_width() or 390))
        height = max(165, int(canvas.winfo_height() or 185))

        left = 42
        right = width - 14
        top = 14
        bottom = height - 34
        plot_w = right - left
        plot_h = bottom - top

        try:
            on_min = parse_hhmm_to_minutes(self.diurnal_on_var.get())
            off_min = parse_hhmm_to_minutes(self.diurnal_off_var.get())
            duration = (off_min - on_min) % (24 * 60)

            if duration <= 0:
                raise ValueError("Diurnal ON and OFF cannot be the same.")

            white_scale = max(0.0, min(100.0, float(self.white_peak_scale_var.get())))
            red_scale = max(0.0, min(100.0, float(self.red_peak_scale_var.get())))
            power = max(0.0, min(5.0, float(self.curve_power_var.get())))

            # Axes and grid
            canvas.create_rectangle(left, top, right, bottom, outline="#cccccc")
            for y_frac, label in [(0.0, "0"), (0.5, "50"), (1.0, "100")]:
                y = bottom - y_frac * plot_h
                canvas.create_line(left, y, right, y, fill="#eeeeee")
                canvas.create_text(left - 8, y, text=label, anchor="e", font=("Segoe UI", 8))

            # X ticks: start, peak, end
            peak_min = (on_min + duration / 2.0) % (24 * 60)
            tick_data = [
                (0.0, minutes_to_hhmm(on_min)),
                (0.5, minutes_to_hhmm(round(peak_min))),
                (1.0, minutes_to_hhmm(off_min)),
            ]
            for frac, label in tick_data:
                x = left + frac * plot_w
                canvas.create_line(x, bottom, x, bottom + 4, fill="#888888")
                canvas.create_text(x, bottom + 16, text=label, anchor="n", font=("Segoe UI", 8))

            # Curves
            white_points = []
            red_points = []
            factor_points = []
            steps = 96

            for i in range(steps + 1):
                frac = i / steps
                tmin = (on_min + frac * duration) % (24 * 60)
                factor = self._diurnal_factor_at_minutes(tmin)
                white = factor * white_scale
                red = factor * red_scale

                x = left + frac * plot_w
                yw = bottom - (white / 100.0) * plot_h
                yr = bottom - (red / 100.0) * plot_h
                yf = bottom - factor * plot_h

                white_points.extend([x, yw])
                red_points.extend([x, yr])
                factor_points.extend([x, yf])

            if len(factor_points) >= 4:
                canvas.create_line(*factor_points, fill="#555555", width=1, dash=(4, 3))
            if len(white_points) >= 4:
                canvas.create_line(*white_points, fill="#1f77b4", width=2)
            if len(red_points) >= 4:
                canvas.create_line(*red_points, fill="#d62728", width=2)

            # Current time marker
            now = time.localtime()
            now_min = now.tm_hour * 60 + now.tm_min + now.tm_sec / 60.0
            elapsed_now = (now_min - on_min) % (24 * 60)
            if elapsed_now <= duration:
                x_now = left + (elapsed_now / duration) * plot_w
                canvas.create_line(x_now, top, x_now, bottom, fill="#222222", dash=(2, 2))
                canvas.create_text(x_now + 3, top + 4, text="now", anchor="nw", font=("Segoe UI", 8))

            # Legend
            legend_y = top + 4
            canvas.create_line(right - 130, legend_y, right - 105, legend_y, fill="#1f77b4", width=2)
            canvas.create_text(right - 100, legend_y, text="White scale", anchor="w", font=("Segoe UI", 8))
            canvas.create_line(right - 130, legend_y + 15, right - 105, legend_y + 15, fill="#d62728", width=2)
            canvas.create_text(right - 100, legend_y + 15, text="Red scale", anchor="w", font=("Segoe UI", 8))
            canvas.create_line(right - 130, legend_y + 30, right - 105, legend_y + 30, fill="#555555", width=1, dash=(4, 3))
            canvas.create_text(right - 100, legend_y + 30, text="Factor", anchor="w", font=("Segoe UI", 8))

            current_factor = self._diurnal_factor()
            self.diurnal_factor_var.set(f"{current_factor:.3f}")
            self.preview_info_var.set(
                f"Preview: {minutes_to_hhmm(on_min)}–{minutes_to_hhmm(off_min)}, "
                f"peak {minutes_to_hhmm(round(peak_min))}, power {power:.2f}, "
                f"white max {white_scale:.1f}%, red max {red_scale:.1f}%"
            )

        except Exception as e:
            canvas.create_text(
                width / 2,
                height / 2,
                text=f"Preview unavailable: {e}",
                anchor="center",
                fill="#a00000",
                font=("Segoe UI", 9),
            )
            self.preview_info_var.set(f"Preview error: {e}")

    def _build_log_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Log", padding=8)
        frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(frame, height=12, wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")

        self.log_text.configure(yscrollcommand=scroll.set)

    def _build_channel_panel(self, parent):
        outer = ttk.LabelFrame(parent, text="Per-channel LED control", padding=8)
        outer.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        for dev in self.expected_devices:
            tab = ttk.Frame(self.notebook, padding=8)
            self.notebook.add(tab, text=dev)
            self._build_device_tab(tab, dev)

    def _build_device_tab(self, tab, dev):
        self.channel_vars.setdefault(dev, {})
        self.channel_widgets.setdefault(dev, {})
        self.dac_all_vars.setdefault(dev, {})

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(top, text=dev, font=("Segoe UI", 12, "bold")).pack(side="left")

        ttk.Button(top, text="Apply this device", command=lambda d=dev: self.apply_device(d)).pack(side="right", padx=2)
        ttk.Button(top, text="OFF this device", command=lambda d=dev: self.off_device(d)).pack(side="right", padx=2)
        ttk.Button(top, text="Status", command=lambda d=dev: self.status_device(d)).pack(side="right", padx=2)

        # Device-level Set All To control
        override = ttk.LabelFrame(tab, text="Device-level override", padding=8)
        override.pack(fill="x", pady=(0, 8))

        ttk.Label(override, text="Set all DACs/channels to").pack(side="left", padx=(0, 8))

        device_all_var = tk.DoubleVar(value=0.0)
        self.device_all_vars[dev] = device_all_var

        ttk.Scale(override, from_=0, to=100, variable=device_all_var).pack(side="left", fill="x", expand=True, padx=4)

        ttk.Spinbox(override, from_=0, to=100, increment=0.1, textvariable=device_all_var, width=7).pack(side="left", padx=4)

        ttk.Button(override, text="Set All To", command=lambda d=dev: self.apply_device_set_all(d)).pack(side="left", padx=4)

        # DAC/channel grid
        grid = ttk.Frame(tab)
        grid.pack(fill="both", expand=True)

        for c, dac in enumerate(DAC_ADDRS):
            dac_frame = ttk.LabelFrame(grid, text=f"DAC {dac}", padding=8)
            dac_frame.grid(row=0, column=c, sticky="nsew", padx=4, pady=4)

            grid.columnconfigure(c, weight=1)
            grid.rowconfigure(0, weight=1)

            self.channel_vars[dev].setdefault(dac, {})
            self.channel_widgets[dev].setdefault(dac, {})

            # DAC-level Set All control
            dac_all_frame = ttk.LabelFrame(dac_frame, text=f"{dac} set all channels", padding=6)
            dac_all_frame.pack(fill="x", pady=(0, 8))

            dac_all_var = tk.DoubleVar(value=0.0)
            self.dac_all_vars[dev][dac] = dac_all_var

            ttk.Label(dac_all_frame, text="All").pack(side="left", padx=(0, 4))
            ttk.Scale(dac_all_frame, from_=0, to=100, variable=dac_all_var).pack(side="left", fill="x", expand=True, padx=4)
            ttk.Spinbox(dac_all_frame, from_=0, to=100, increment=0.1, textvariable=dac_all_var, width=7).pack(side="left", padx=4)

            ttk.Button(
                dac_all_frame,
                text="Set all",
                command=lambda d=dev, a=dac: self.apply_dac_set_all(d, a),
            ).pack(side="left", padx=4)

            ttk.Button(
                dac_frame,
                text=f"Apply {dac}",
                command=lambda d=dev, a=dac: self.apply_dac(d, a),
            ).pack(fill="x", pady=(0, 8))

            for ch in CHANNELS:
                row = ttk.Frame(dac_frame)
                row.pack(fill="x", pady=5)

                ttk.Label(row, text=f"Ch {ch}", width=5).pack(side="left")

                value = float(self.led_settings.get(dev, {}).get(dac, {}).get(ch, 0.0))
                var = tk.DoubleVar(value=value)
                self.channel_vars[dev][dac][ch] = var

                ttk.Scale(
                    row,
                    from_=0,
                    to=100,
                    variable=var,
                    command=lambda _val, d=dev, a=dac, c=ch: self._on_slider_move(d, a, c),
                ).pack(side="left", fill="x", expand=True, padx=4)

                ttk.Spinbox(
                    row,
                    from_=0,
                    to=100,
                    increment=0.1,
                    textvariable=var,
                    width=7,
                    command=lambda d=dev, a=dac, c=ch: self._on_spinbox_change(d, a, c),
                ).pack(side="left", padx=2)

                ttk.Button(
                    row,
                    text="Set",
                    width=5,
                    command=lambda d=dev, a=dac, c=ch: self.apply_channel(d, a, c),
                ).pack(side="left", padx=2)

    # ======================================================
    # Logging and threading
    # ======================================================

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {msg}")

    def _poll_log_queue(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        self.after(100, self._poll_log_queue)

    def run_worker(self, title, func):
        def wrapper():
            with self.worker_lock:
                self.log(f"START: {title}")
                try:
                    func()
                except Exception as e:
                    self.log(f"ERROR in {title}: {e}")
                finally:
                    self.log(f"END: {title}")
                    self.after(0, self.update_connected_status)

        threading.Thread(target=wrapper, daemon=True).start()

    # ======================================================
    # Utility functions
    # ======================================================

    def get_manual_ports_from_ui(self):
        ports = {}
        for dev, var in self.port_vars.items():
            value = var.get().strip()
            if value:
                ports[dev] = value
        return ports

    def _sync_vars_to_settings(self):
        for dev in self.expected_devices:
            self.led_settings.setdefault(dev, {})
            for dac in DAC_ADDRS:
                self.led_settings[dev].setdefault(dac, {})
                for ch in CHANNELS:
                    var = self.channel_vars.get(dev, {}).get(dac, {}).get(ch)
                    if var is not None:
                        self.led_settings[dev][dac][ch] = round(float(var.get()), 3)

    def ensure_led_object(self):
        if self.led is None:
            self.led = DreamLEDPC(
                expected_devices=self.expected_devices,
                baudrate=int(self.baudrate_var.get()),
                connect_delay_s=float(self.connect_delay_var.get()),
                command_delay_s=float(self.command_delay_var.get()),
                read_extra_s=float(self.read_extra_var.get()),
            )
        return self.led

    def maybe_connect_for_command(self):
        led = self.ensure_led_object()
        if not led.devices:
            led.connect_manual_ports(self.get_manual_ports_from_ui())
        return led

    def maybe_disconnect_after_command(self):
        if self.auto_disconnect_var.get() and self.led is not None:
            self.led.close()

    def update_connected_status(self):
        devices = set()
        if self.led is not None:
            devices = set(self.led.devices.keys())
        self.connected_devices = devices

        for dev in self.expected_devices:
            if dev in devices:
                self.connected_label_vars[dev].set("Connected")
            else:
                self.connected_label_vars[dev].set("Not connected")

    def _device_scale_for_diurnal(self, dev):
        d = dev.upper()
        if d.startswith("WHITE_LED"):
            return float(self.white_peak_scale_var.get()) / 100.0
        if d.startswith("RED_LED"):
            return float(self.red_peak_scale_var.get()) / 100.0
        if d.startswith("UVIR_LED"):
            if self.include_uvir_var.get():
                return float(self.white_peak_scale_var.get()) / 100.0
            return None
        return float(self.white_peak_scale_var.get()) / 100.0

    # ======================================================
    # Presets
    # ======================================================

    def save_preset_json(self):
        self._sync_vars_to_settings()
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save LED preset",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.led_settings, f, indent=2)
        self.log(f"Saved preset: {path}")

    def load_preset_json(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Load LED preset",
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            settings = json.load(f)

        for dev, dac_block in settings.items():
            self.led_settings.setdefault(dev, {})
            for dac, channel_block in dac_block.items():
                self.led_settings[dev].setdefault(dac, {})
                self.led_settings[dev][dac].update(channel_block)

        self._apply_settings_to_gui_vars()
        self.log(f"Loaded preset: {path}")

    def _apply_settings_to_gui_vars(self):
        for dev in self.expected_devices:
            for dac in DAC_ADDRS:
                for ch in CHANNELS:
                    var = self.channel_vars.get(dev, {}).get(dac, {}).get(ch)
                    if var is not None:
                        value = float(self.led_settings.get(dev, {}).get(dac, {}).get(ch, 0.0))
                        var.set(value)

    # ======================================================
    # Connection commands
    # ======================================================

    def list_ports(self):
        def task():
            led = self.ensure_led_object()
            ports = led.list_ports()
            if not ports:
                self.log("No serial ports detected.")
            else:
                for p in ports:
                    self.log(f"PORT: {p.device} | {p.description}")
        self.run_worker("List COM ports", task)

    def connect_selected(self):
        def task():
            if self.led is not None:
                self.led.close()

            self.led = DreamLEDPC(
                expected_devices=self.expected_devices,
                baudrate=int(self.baudrate_var.get()),
                connect_delay_s=float(self.connect_delay_var.get()),
                command_delay_s=float(self.command_delay_var.get()),
                read_extra_s=float(self.read_extra_var.get()),
            )
            self.led.connect_manual_ports(self.get_manual_ports_from_ui())
            self._log_connected_summary()

        self.run_worker("Connect selected COM ports", task)

    def auto_scan(self):
        def task():
            if self.led is not None:
                self.led.close()

            self.led = DreamLEDPC(
                expected_devices=self.expected_devices,
                baudrate=int(self.baudrate_var.get()),
                connect_delay_s=float(self.connect_delay_var.get()),
                command_delay_s=float(self.command_delay_var.get()),
                read_extra_s=float(self.read_extra_var.get()),
            )
            self.led.scan_and_connect()
            self._log_connected_summary()

        self.run_worker("Auto scan", task)

    def _log_connected_summary(self):
        if self.led is None or not self.led.devices:
            self.log("Connected devices: none")
        else:
            for dev, ser in self.led.devices.items():
                self.log(f"Connected: {dev} on {ser.port}")

    def disconnect(self):
        def task():
            if self.led is not None:
                self.led.close()
            self.log("Disconnected.")
        self.run_worker("Disconnect", task)

    # ======================================================
    # Fixed/global LED commands
    # ======================================================

    def set_time_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.set_time_from_pc_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("Set PC time on all connected", task)

    def apply_fixed_regime(self):
        def task():
            self._sync_vars_to_settings()
            led = self.maybe_connect_for_command()
            led.set_time_from_pc_all_connected()
            led.set_schedule_all_connected(self.light_on_var.get(), self.light_off_var.get())
            led.schedule_on_all_connected()
            led.apply_nested_settings(self.led_settings)
            self.maybe_disconnect_after_command()
        self.run_worker("Apply fixed constant regime", task)

    def set_schedule_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.set_schedule_all_connected(self.light_on_var.get(), self.light_off_var.get())
            self.maybe_disconnect_after_command()
        self.run_worker("Set fixed schedule", task)

    def schedule_on_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.schedule_on_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("Schedule ON", task)

    def schedule_off_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.schedule_off_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("Schedule OFF", task)

    def apply_all_channels(self):
        def task():
            self._sync_vars_to_settings()
            led = self.maybe_connect_for_command()
            led.apply_nested_settings(self.led_settings)
            self.maybe_disconnect_after_command()
        self.run_worker("Apply all shown channel values", task)

    def status_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.status_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("Status all", task)

    def i2c_scan_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.i2c_scan_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("I2C scan all", task)

    def on_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.on_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("ON restore targets", task)

    def off_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.off_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("OFF all outputs", task)

    def apply_heartbeat(self):
        def task():
            led = self.maybe_connect_for_command()
            mode = self.heartbeat_var.get().upper()
            led.set_heartbeat_all_connected(mode)
            self.maybe_disconnect_after_command()
        self.run_worker("Apply heartbeat", task)

    # ======================================================
    # Diurnal regime
    # ======================================================

    def _update_auto_peak(self):
        try:
            on_min = parse_hhmm_to_minutes(self.diurnal_on_var.get())
            off_min = parse_hhmm_to_minutes(self.diurnal_off_var.get())

            duration = (off_min - on_min) % (24 * 60)
            if duration == 0:
                peak_min = on_min
            else:
                peak_min = (on_min + duration / 2.0) % (24 * 60)

            self.diurnal_peak_var.set(minutes_to_hhmm(round(peak_min)))
            self._update_diurnal_factor_label()
            self._draw_diurnal_preview()
        except Exception as e:
            self.log(f"Could not update diurnal peak: {e}")

    def _diurnal_factor(self, now_min=None):
        on_min = parse_hhmm_to_minutes(self.diurnal_on_var.get())
        off_min = parse_hhmm_to_minutes(self.diurnal_off_var.get())

        if now_min is None:
            t = time.localtime()
            now_min = int(t.tm_hour) * 60 + int(t.tm_min) + int(t.tm_sec) / 60.0

        duration = (off_min - on_min) % (24 * 60)
        if duration <= 0:
            return 0.0

        elapsed = (now_min - on_min) % (24 * 60)
        if elapsed < 0 or elapsed > duration:
            return 0.0

        phase = elapsed / duration
        if phase < 0.0 or phase > 1.0:
            return 0.0

        base = math.sin(math.pi * phase)
        if base < 0:
            base = 0.0

        power = max(0.0, min(5.0, float(self.curve_power_var.get())))

        if power == 0.0:
            return 1.0 if 0.0 <= phase <= 1.0 else 0.0

        return max(0.0, min(1.0, base ** power))

    def _update_diurnal_factor_label(self):
        try:
            factor = self._diurnal_factor()
            self.diurnal_factor_var.set(f"{factor:.3f}")
        except Exception:
            self.diurnal_factor_var.set("ERR")

    def _build_diurnal_scaled_settings(self):
        self._sync_vars_to_settings()
        factor = self._diurnal_factor()
        self.diurnal_factor_var.set(f"{factor:.3f}")

        scaled = {}
        for dev in self.expected_devices:
            scale = self._device_scale_for_diurnal(dev)

            if scale is None:
                # Keep UV/IR as shown values if not included in diurnal scaling.
                continue

            scaled.setdefault(dev, {})
            for dac in DAC_ADDRS:
                scaled[dev].setdefault(dac, {})
                for ch in CHANNELS:
                    base_value = float(self.led_settings.get(dev, {}).get(dac, {}).get(ch, 0.0))
                    scaled_value = base_value * factor * scale
                    if scaled_value < 0:
                        scaled_value = 0.0
                    if scaled_value > 100:
                        scaled_value = 100.0
                    scaled[dev][dac][ch] = round(scaled_value, 3)

        return scaled, factor

    def apply_diurnal_now(self):
        def task():
            self._update_auto_peak()
            scaled, factor = self._build_diurnal_scaled_settings()

            led = self.maybe_connect_for_command()

            # Disable RP2040 local fixed schedule during PC-driven diurnal mode.
            led.schedule_off_all_connected()

            if factor <= 0:
                # Send zero output to diurnal-controlled devices.
                for dev in scaled:
                    if dev in led.devices:
                        led.set_all_on_device(dev, 0.0)
                self.log("Diurnal factor is 0. Diurnal-controlled outputs set to 0%.")
            else:
                for dev, dac_block in scaled.items():
                    if dev not in led.devices:
                        self.log(f"{dev} is not connected; skipped.")
                        continue
                    for dac, channel_block in dac_block.items():
                        for ch, value in channel_block.items():
                            led.set_channel(dev, dac, ch, value)

                self.log(f"Applied diurnal values. Factor={factor:.3f}")

            self.maybe_disconnect_after_command()

        self.run_worker("Apply diurnal now", task)

    def start_diurnal(self):
        if self.diurnal_running:
            self.log("Diurnal mode is already running.")
            return

        self.diurnal_running = True
        self.diurnal_mode_status_var.set("Running")
        self.log("Started PC-driven diurnal mode.")
        self._schedule_next_diurnal_tick(immediate=True)

    def stop_diurnal(self):
        self.diurnal_running = False
        self.diurnal_mode_status_var.set("Stopped")

        if self.diurnal_after_id is not None:
            try:
                self.after_cancel(self.diurnal_after_id)
            except Exception:
                pass
            self.diurnal_after_id = None

        self.log("Stopped PC-driven diurnal mode.")

    def _schedule_next_diurnal_tick(self, immediate=False):
        if not self.diurnal_running:
            return

        delay_ms = 0 if immediate else max(5, int(self.diurnal_interval_var.get())) * 1000
        self.diurnal_after_id = self.after(delay_ms, self._diurnal_tick)

    def _diurnal_tick(self):
        if not self.diurnal_running:
            return

        def task():
            self._update_auto_peak()
            scaled, factor = self._build_diurnal_scaled_settings()
            led = self.maybe_connect_for_command()
            led.schedule_off_all_connected()

            if factor <= 0:
                for dev in scaled:
                    if dev in led.devices:
                        led.set_all_on_device(dev, 0.0)
            else:
                for dev, dac_block in scaled.items():
                    if dev not in led.devices:
                        continue
                    for dac, channel_block in dac_block.items():
                        for ch, value in channel_block.items():
                            led.set_channel(dev, dac, ch, value)

            self.diurnal_mode_status_var.set(f"Running | factor={factor:.3f} | {time.strftime('%H:%M:%S')}")
            self.after(0, self._draw_diurnal_preview)
            self.maybe_disconnect_after_command()

        self.run_worker("Diurnal update", task)
        self._schedule_next_diurnal_tick(immediate=False)

    # ======================================================
    # Per-device / DAC / channel commands
    # ======================================================

    def apply_device(self, dev):
        def task():
            self._sync_vars_to_settings()
            led = self.maybe_connect_for_command()

            if dev not in led.devices:
                self.log(f"{dev} is not connected; skipped.")
            else:
                for dac in DAC_ADDRS:
                    for ch in CHANNELS:
                        value = self.led_settings[dev][dac][ch]
                        led.set_channel(dev, dac, ch, value)

            self.maybe_disconnect_after_command()

        self.run_worker(f"Apply {dev}", task)

    def apply_device_set_all(self, dev):
        def task():
            value = round(float(self.device_all_vars[dev].get()), 3)

            self.led_settings.setdefault(dev, {})
            for dac in DAC_ADDRS:
                self.led_settings[dev].setdefault(dac, {})

                dac_all_var = self.dac_all_vars.get(dev, {}).get(dac)
                if dac_all_var is not None:
                    dac_all_var.set(value)

                for ch in CHANNELS:
                    self.led_settings[dev][dac][ch] = value
                    var = self.channel_vars.get(dev, {}).get(dac, {}).get(ch)
                    if var is not None:
                        var.set(value)

            led = self.maybe_connect_for_command()

            if dev not in led.devices:
                self.log(f"{dev} is not connected; skipped.")
            else:
                led.set_all_on_device(dev, value)
                self.log(f"{dev}: set all DACs/channels to {value}%")

            self.maybe_disconnect_after_command()

        self.run_worker(f"Set all channels on {dev}", task)

    def apply_dac(self, dev, dac):
        def task():
            self._sync_vars_to_settings()
            led = self.maybe_connect_for_command()

            if dev not in led.devices:
                self.log(f"{dev} is not connected; skipped.")
            else:
                for ch in CHANNELS:
                    value = self.led_settings[dev][dac][ch]
                    led.set_channel(dev, dac, ch, value)

            self.maybe_disconnect_after_command()

        self.run_worker(f"Apply {dev} {dac}", task)

    def apply_dac_set_all(self, dev, dac):
        def task():
            value = round(float(self.dac_all_vars[dev][dac].get()), 3)

            self.led_settings.setdefault(dev, {})
            self.led_settings[dev].setdefault(dac, {})

            for ch in CHANNELS:
                self.led_settings[dev][dac][ch] = value
                var = self.channel_vars.get(dev, {}).get(dac, {}).get(ch)
                if var is not None:
                    var.set(value)

            led = self.maybe_connect_for_command()

            if dev not in led.devices:
                self.log(f"{dev} is not connected; skipped.")
            else:
                led.set_all_channels_on_dac(dev, dac, value)
                self.log(f"{dev} {dac}: set all channels to {value}%")

            self.maybe_disconnect_after_command()

        self.run_worker(f"Set all channels on {dev} {dac}", task)

    def apply_channel(self, dev, dac, ch):
        def task():
            value = round(float(self.channel_vars[dev][dac][ch].get()), 3)
            self.led_settings.setdefault(dev, {}).setdefault(dac, {})[ch] = value

            led = self.maybe_connect_for_command()

            if dev not in led.devices:
                self.log(f"{dev} is not connected; skipped.")
            else:
                led.set_channel(dev, dac, ch, value)

            self.maybe_disconnect_after_command()

        self.run_worker(f"Set {dev} {dac} {ch}", task)

    def off_device(self, dev):
        def task():
            led = self.maybe_connect_for_command()

            if dev in led.devices:
                led.off(dev)
            else:
                self.log(f"{dev} is not connected; skipped.")

            self.maybe_disconnect_after_command()

        self.run_worker(f"OFF {dev}", task)

    def status_device(self, dev):
        def task():
            led = self.maybe_connect_for_command()

            if dev in led.devices:
                led.status(dev)
            else:
                self.log(f"{dev} is not connected; skipped.")

            self.maybe_disconnect_after_command()

        self.run_worker(f"Status {dev}", task)

    def _on_slider_move(self, dev, dac, ch):
        try:
            value = round(float(self.channel_vars[dev][dac][ch].get()), 3)
            self.led_settings.setdefault(dev, {}).setdefault(dac, {})[ch] = value
        except Exception:
            return

        if self.live_update_var.get():
            key = f"_debounce_{dev}_{dac}_{ch}"
            old_after = getattr(self, key, None)
            if old_after is not None:
                try:
                    self.after_cancel(old_after)
                except Exception:
                    pass
            after_id = self.after(250, lambda: self.apply_channel(dev, dac, ch))
            setattr(self, key, after_id)

    def _on_spinbox_change(self, dev, dac, ch):
        self._on_slider_move(dev, dac, ch)

    # ======================================================
    # Close
    # ======================================================

    def on_close(self):
        self.stop_diurnal()
        try:
            self.save_gui_config()
        except Exception:
            pass
        try:
            if self.led is not None:
                self.led.close()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = DreamLEDControlPanel()
    app.mainloop()
