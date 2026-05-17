# DREAM_LED_control_panel.py
# GUI control panel for DREAM LED RP2040 controllers
#
# Put this file in:
#   C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_LED_control
#
# Run with:
#   py DREAM_LED_control_panel.py
# or use the supplied .bat launcher.
#
# Requirements:
#   py -m pip install pyserial
#
# This GUI uses DREAM_LED_PC.py in the same folder.
# It controls each DAC channel on each connected RP2040 controller.

import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from DREAM_LED_PC import DreamLEDPC

# Try to import defaults from run_dream_led.py in the same folder.
try:
    import run_dream_led as default_config
except Exception:
    default_config = None


APP_TITLE = "DREAM LED Control Panel"
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

    # Ensure all expected devices exist in settings/ports.
    for dev in expected:
        ports.setdefault(dev, "")
        settings.setdefault(dev, {})
        for dac in DAC_ADDRS:
            settings[dev].setdefault(dac, {})
            for ch in CHANNELS:
                settings[dev][dac].setdefault(ch, 0.0)

    return expected, ports, settings


class DreamLEDControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1320x820")
        self.minsize(1100, 700)

        self.expected_devices, self.manual_ports, self.led_settings = load_defaults_from_run_file()

        self.connected_devices = set()
        self.led = None
        self.worker_lock = threading.Lock()
        self.log_queue = queue.Queue()

        self.baudrate_var = tk.IntVar(value=115200)
        self.connect_delay_var = tk.DoubleVar(value=2.0)
        self.command_delay_var = tk.DoubleVar(value=0.20)
        self.read_extra_var = tk.DoubleVar(value=0.20)
        self.light_on_var = tk.StringVar(value="08:00")
        self.light_off_var = tk.StringVar(value="20:00")
        self.heartbeat_var = tk.StringVar(value="ON")
        self.live_update_var = tk.BooleanVar(value=False)
        self.auto_disconnect_var = tk.BooleanVar(value=False)

        self.port_vars = {}
        self.connected_label_vars = {}
        self.channel_vars = {}
        self.channel_widgets = {}

        self._load_gui_config_if_available()
        self._build_ui()
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

        ttk.Label(top, text="DREAM LED Control Panel", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Button(top, text="Save GUI config", command=self.save_gui_config).pack(side="right", padx=4)
        ttk.Button(top, text="Load preset JSON", command=self.load_preset_json).pack(side="right", padx=4)
        ttk.Button(top, text="Save preset JSON", command=self.save_preset_json).pack(side="right", padx=4)

        main_pane = ttk.PanedWindow(root, orient="horizontal")
        main_pane.pack(fill="both", expand=True)

        left = ttk.Frame(main_pane, width=380)
        right = ttk.Frame(main_pane)
        main_pane.add(left, weight=0)
        main_pane.add(right, weight=1)

        self._build_connection_panel(left)
        self._build_schedule_panel(left)
        self._build_log_panel(left)
        self._build_channel_panel(right)

    def _build_connection_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Connection", padding=8)
        frame.pack(fill="x", pady=(0, 8))

        ttk.Label(frame, text="Baudrate").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.baudrate_var, width=10).grid(row=0, column=1, sticky="w")

        ttk.Label(frame, text="Connect delay s").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.connect_delay_var, width=10).grid(row=1, column=1, sticky="w")

        ttk.Checkbutton(frame, text="Disconnect after each command", variable=self.auto_disconnect_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 4)
        )

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
        frame = ttk.LabelFrame(parent, text="Schedule and global controls", padding=8)
        frame.pack(fill="x", pady=(0, 8))

        ttk.Label(frame, text="Light ON").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.light_on_var, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(frame, text="Light OFF").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Entry(frame, textvariable=self.light_off_var, width=8).grid(row=0, column=3, sticky="w")

        ttk.Button(frame, text="Set PC time", command=self.set_time_all).grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(frame, text="Set schedule", command=self.set_schedule_all).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="Schedule ON", command=self.schedule_on_all).grid(row=1, column=2, sticky="ew", pady=4)
        ttk.Button(frame, text="Schedule OFF", command=self.schedule_off_all).grid(row=1, column=3, sticky="ew", pady=4)

        ttk.Button(frame, text="Apply all shown values", command=self.apply_all_channels).grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(frame, text="Status all", command=self.status_all).grid(row=2, column=2, sticky="ew", pady=4)
        ttk.Button(frame, text="I2C scan", command=self.i2c_scan_all).grid(row=2, column=3, sticky="ew", pady=4)

        ttk.Button(frame, text="ON restore targets", command=self.on_all).grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(frame, text="OFF all outputs", command=self.off_all).grid(row=3, column=2, columnspan=2, sticky="ew", pady=4)

        ttk.Label(frame, text="Heartbeat").grid(row=4, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self.heartbeat_var, values=["ON", "OFF"], width=8, state="readonly").grid(
            row=4, column=1, sticky="w"
        )
        ttk.Button(frame, text="Apply heartbeat", command=self.apply_heartbeat).grid(row=4, column=2, columnspan=2, sticky="ew", pady=4)

        ttk.Checkbutton(frame, text="Live update when sliders move", variable=self.live_update_var).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(4, 0)
        )

        for i in range(4):
            frame.columnconfigure(i, weight=1)

    def _build_log_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Log", padding=8)
        frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(frame, height=18, wrap="word")
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

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text=dev, font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(top, text="Apply this device", command=lambda d=dev: self.apply_device(d)).pack(side="right", padx=2)
        ttk.Button(top, text="OFF this device", command=lambda d=dev: self.off_device(d)).pack(side="right", padx=2)
        ttk.Button(top, text="Status", command=lambda d=dev: self.status_device(d)).pack(side="right", padx=2)

        grid = ttk.Frame(tab)
        grid.pack(fill="both", expand=True)

        for c, dac in enumerate(DAC_ADDRS):
            dac_frame = ttk.LabelFrame(grid, text=f"DAC {dac}", padding=8)
            dac_frame.grid(row=0, column=c, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(c, weight=1)

            self.channel_vars[dev].setdefault(dac, {})
            self.channel_widgets[dev].setdefault(dac, {})

            ttk.Button(dac_frame, text=f"Apply {dac}", command=lambda d=dev, a=dac: self.apply_dac(d, a)).pack(
                fill="x", pady=(0, 8)
            )

            for ch in CHANNELS:
                row = ttk.Frame(dac_frame)
                row.pack(fill="x", pady=5)

                ttk.Label(row, text=f"Ch {ch}", width=5).pack(side="left")

                value = float(self.led_settings.get(dev, {}).get(dac, {}).get(ch, 0.0))
                var = tk.DoubleVar(value=value)
                self.channel_vars[dev][dac][ch] = var

                scale = ttk.Scale(
                    row,
                    from_=0,
                    to=100,
                    variable=var,
                    command=lambda _val, d=dev, a=dac, c=ch: self._on_slider_move(d, a, c),
                )
                scale.pack(side="left", fill="x", expand=True, padx=4)

                spin = ttk.Spinbox(
                    row,
                    from_=0,
                    to=100,
                    increment=0.1,
                    textvariable=var,
                    width=6,
                    command=lambda d=dev, a=dac, c=ch: self._on_spinbox_change(d, a, c),
                )
                spin.pack(side="left", padx=2)

                btn = ttk.Button(
                    row,
                    text="Set",
                    width=5,
                    command=lambda d=dev, a=dac, c=ch: self.apply_channel(d, a, c),
                )
                btn.pack(side="left", padx=2)

                self.channel_widgets[dev][dac][ch] = [scale, spin, btn]

        grid.rowconfigure(0, weight=1)

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

    def require_connected(self, dev=None):
        if self.led is None or not self.led.devices:
            self.log("No controllers connected. Press 'Connect selected' first.")
            return False
        if dev is not None and dev not in self.led.devices:
            self.log(f"{dev} is not connected; command skipped.")
            return False
        return True

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
    # Global LED commands
    # ======================================================

    def set_time_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.set_time_from_pc_all_connected()
            self.maybe_disconnect_after_command()
        self.run_worker("Set PC time on all connected", task)

    def set_schedule_all(self):
        def task():
            led = self.maybe_connect_for_command()
            led.set_schedule_all_connected(self.light_on_var.get(), self.light_off_var.get())
            self.maybe_disconnect_after_command()
        self.run_worker("Set schedule", task)

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
    # Per device / DAC / channel commands
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
        # Keep JSON/settings updated, but only send if live update is enabled.
        try:
            value = round(float(self.channel_vars[dev][dac][ch].get()), 3)
            self.led_settings.setdefault(dev, {}).setdefault(dac, {})[ch] = value
        except Exception:
            return

        if self.live_update_var.get():
            # Avoid creating very high command rates. Debounce per channel.
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
