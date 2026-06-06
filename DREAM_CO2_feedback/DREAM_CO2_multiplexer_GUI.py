# DREAM_CO2_multiplexer_GUI.py
# PC GUI for DREAM CO2 multiplexer using USB serial JSON commands.
# No Wi-Fi and no HTTP URL are required.
# Install: python -m pip install pyserial

import json
import time
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

SETTINGS_FILE = Path("DREAM_CO2_multiplexer_settings.json")

DEFAULT_SETTINGS = {
    "serial_port": "COM12",
    "baudrate": 115200,
    "last_sample": "1,2,3",
    "purge_s": 15.0,
    "record_s": 30.0,
    "cycle_samples": ["1", "2", "3", "1,2,3"],
}

VALID_SAMPLE_EXAMPLES = "1, 2, 3, 1,2, 1,3, 2,3, or 1,2,3"


def normalise_sample_text(text):
    parts = []
    for token in str(text).replace(";", ",").replace("+", ",").replace("/", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            valve = int(token)
        except ValueError:
            raise ValueError("Use valve numbers only: %s" % VALID_SAMPLE_EXAMPLES)
        if valve not in (1, 2, 3):
            raise ValueError("Valve number must be 1, 2 or 3")
        if valve not in parts:
            parts.append(valve)
    if not parts:
        raise ValueError("At least one valve must be selected for the LI-850")
    return ",".join(str(v) for v in sorted(parts))


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            out = dict(DEFAULT_SETTINGS)
            out.update(data)
            # Backward compatibility with old mode names.
            old_mode = out.pop("last_mode", None)
            if old_mode and "last_sample" not in data:
                out["last_sample"] = mode_to_sample(old_mode)
            if "cycle_positions" in out and "cycle_samples" not in data:
                out["cycle_samples"] = [mode_to_sample(m) for m in out.get("cycle_positions", [])]
            out["last_sample"] = normalise_sample_text(out.get("last_sample", "1,2,3"))
            cleaned = []
            for item in out.get("cycle_samples", []):
                try:
                    cleaned.append(normalise_sample_text(item))
                except Exception:
                    pass
            out["cycle_samples"] = cleaned or ["1", "2", "3", "1,2,3"]
            return out
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def mode_to_sample(mode):
    mode = str(mode).strip().upper()
    if mode in ("POSITION_1", "P1"):
        return "1"
    if mode in ("POSITION_2", "P2"):
        return "2"
    if mode in ("POSITION_3", "P3"):
        return "3"
    return "1,2,3"


def save_settings(settings):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


class MultiplexerSerialClient:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.reader_thread = None
        self.running = False
        self.on_message = None

    def connect(self, port, baudrate):
        if serial is None:
            raise RuntimeError("pyserial is not installed. Run: python -m pip install pyserial")
        self.close()
        self.ser = serial.Serial(port=port, baudrate=int(baudrate), timeout=0.2)
        self.running = True
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        time.sleep(1.0)
        self.send({"cmd": "identify"})
        self.send({"cmd": "status"})

    def close(self):
        self.running = False
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def send(self, obj):
        if self.ser is None or not self.ser.is_open:
            raise RuntimeError("Serial port is not connected")
        line = json.dumps(obj) + "\n"
        with self.lock:
            self.ser.write(line.encode("utf-8"))
            self.ser.flush()

    def _reader_loop(self):
        while self.running and self.ser is not None:
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                text = raw.decode(errors="replace").strip()
                if self.on_message:
                    self.on_message(text)
            except Exception as exc:
                if self.on_message:
                    self.on_message("ERROR: %s" % exc)
                time.sleep(0.5)


class MultiplexerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("DREAM CO2 Multiplexer Control")
        self.settings = load_settings()
        self.client = MultiplexerSerialClient()
        self.client.on_message = self.handle_serial_text
        self.cycle_running = False
        self.cycle_thread = None

        self.port_var = tk.StringVar(value=self.settings.get("serial_port", "COM12"))
        self.baud_var = tk.StringVar(value=str(self.settings.get("baudrate", 115200)))
        self.sample_var = tk.StringVar(value=self.settings.get("last_sample", "1,2,3"))
        self.status_var = tk.StringVar(value="Disconnected")
        self.current_sample_var = tk.StringVar(value="Sampling: ?")
        self.valve_status_var = tk.StringVar(value="V1 ?, V2 ?, V3 ?")
        self.purge_var = tk.StringVar(value=str(self.settings.get("purge_s", 15.0)))
        self.record_var = tk.StringVar(value=str(self.settings.get("record_s", 30.0)))
        self.cycle_samples_var = tk.StringVar(value="; ".join(self.settings.get("cycle_samples", ["1", "2", "3", "1,2,3"])))

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        pad = {"padx": 6, "pady": 4}

        frm_conn = ttk.LabelFrame(self.root, text="Serial connection")
        frm_conn.grid(row=0, column=0, sticky="ew", **pad)
        frm_conn.columnconfigure(1, weight=1)

        ttk.Label(frm_conn, text="Port").grid(row=0, column=0, sticky="w", **pad)
        self.port_combo = ttk.Combobox(frm_conn, textvariable=self.port_var, width=20)
        self.port_combo.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm_conn, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, **pad)
        ttk.Button(frm_conn, text="Connect", command=self.connect).grid(row=0, column=3, **pad)
        ttk.Button(frm_conn, text="Disconnect", command=self.disconnect).grid(row=0, column=4, **pad)

        ttk.Label(frm_conn, text="Baud").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm_conn, textvariable=self.baud_var, width=12).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(frm_conn, textvariable=self.status_var).grid(row=1, column=2, columnspan=3, sticky="w", **pad)

        frm_sample = ttk.LabelFrame(self.root, text="Sampling selection")
        frm_sample.grid(row=1, column=0, sticky="ew", **pad)
        frm_sample.columnconfigure(1, weight=1)

        ttk.Label(frm_sample, text="To LI-850").grid(row=0, column=0, sticky="w", **pad)
        sample_entry = ttk.Entry(frm_sample, textvariable=self.sample_var, width=24)
        sample_entry.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm_sample, text="Set sampling", command=self.set_sampling_from_box).grid(row=0, column=2, **pad)
        ttk.Button(frm_sample, text="Query status", command=self.query_status).grid(row=0, column=3, **pad)
        ttk.Label(frm_sample, text="Examples: " + VALID_SAMPLE_EXAMPLES).grid(row=1, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(frm_sample, textvariable=self.current_sample_var, font=("Segoe UI", 10, "bold")).grid(row=2, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(frm_sample, textvariable=self.valve_status_var).grid(row=3, column=0, columnspan=4, sticky="w", **pad)

        frm_cycle = ttk.LabelFrame(self.root, text="Sampling cycle")
        frm_cycle.grid(row=2, column=0, sticky="ew", **pad)
        frm_cycle.columnconfigure(1, weight=1)
        ttk.Label(frm_cycle, text="Cycle samples").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm_cycle, textvariable=self.cycle_samples_var, width=34).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(frm_cycle, text="Use ; between steps, e.g. 1;2;3;1,2,3").grid(row=0, column=2, columnspan=4, sticky="w", **pad)
        ttk.Label(frm_cycle, text="Purge s").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm_cycle, textvariable=self.purge_var, width=8).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(frm_cycle, text="Record s").grid(row=1, column=2, sticky="w", **pad)
        ttk.Entry(frm_cycle, textvariable=self.record_var, width=8).grid(row=1, column=3, **pad)
        ttk.Button(frm_cycle, text="Start cycle", command=self.start_cycle).grid(row=1, column=4, **pad)
        ttk.Button(frm_cycle, text="Stop cycle", command=self.stop_cycle).grid(row=1, column=5, **pad)

        frm_log = ttk.LabelFrame(self.root, text="Log")
        frm_log.grid(row=3, column=0, sticky="nsew", **pad)
        self.root.rowconfigure(3, weight=1)
        self.root.columnconfigure(0, weight=1)
        frm_log.rowconfigure(0, weight=1)
        frm_log.columnconfigure(0, weight=1)
        self.log = tk.Text(frm_log, height=14, width=100)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frm_log, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        self.refresh_ports()

    def refresh_ports(self):
        if list_ports is None:
            self.port_combo["values"] = []
            return
        ports = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def connect(self):
        try:
            self.client.connect(self.port_var.get(), int(self.baud_var.get()))
            self.status_var.set("Connected to %s" % self.port_var.get())
            self.save_current_settings()
        except Exception as exc:
            messagebox.showerror("Connection error", str(exc))
            self.status_var.set("Connection failed")

    def disconnect(self):
        self.stop_cycle(log=False)
        self.client.close()
        self.status_var.set("Disconnected")

    def send_command(self, command):
        try:
            self.client.send(command)
        except Exception as exc:
            messagebox.showerror("Serial command error", str(exc))

    def set_sampling_from_box(self):
        try:
            sample = normalise_sample_text(self.sample_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid sampling selection", str(exc))
            return
        self.sample_var.set(sample)
        self.settings["last_sample"] = sample
        self.save_current_settings()
        self.send_command({"cmd": "sample", "sample": sample})

    def query_status(self):
        self.send_command({"cmd": "status"})

    def parse_cycle_samples(self):
        raw = self.cycle_samples_var.get().strip()
        items = []
        for part in raw.split(";"):
            part = part.strip()
            if not part:
                continue
            items.append(normalise_sample_text(part))
        if not items:
            raise ValueError("Cycle must contain at least one sample step")
        return items

    def save_current_settings(self):
        self.settings["serial_port"] = self.port_var.get()
        self.settings["baudrate"] = int(self.baud_var.get())
        try:
            self.settings["last_sample"] = normalise_sample_text(self.sample_var.get())
        except ValueError:
            pass
        try:
            self.settings["cycle_samples"] = self.parse_cycle_samples()
        except ValueError:
            pass
        try:
            self.settings["purge_s"] = float(self.purge_var.get())
            self.settings["record_s"] = float(self.record_var.get())
        except ValueError:
            pass
        save_settings(self.settings)

    def append_log(self, text):
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", "[%s] %s\n" % (ts, text))
        self.log.see("end")

    def _update_from_simple_status(self, line):
        # Expected: STATUS: 1,3 | V1 LI-850 | V2 bypass | V3 LI-850
        # or:      SAMPLING: 1,3 | V1 LI-850 | V2 bypass | V3 LI-850
        clean = line.strip()
        parts = [p.strip() for p in clean.split("|")]
        if not parts or ":" not in parts[0]:
            return False
        label, sample = [x.strip() for x in parts[0].split(":", 1)]
        if label not in ("STATUS", "SAMPLING"):
            return False

        self.sample_var.set(sample)
        self.current_sample_var.set("Current sampling: %s" % sample)
        valve_text = " | ".join(parts[1:4])
        self.valve_status_var.set(valve_text)
        if label == "SAMPLING":
            self.append_log("Sampling %s: %s" % (sample, valve_text))
        return True

    def handle_serial_text(self, text):
        def update():
            line = text.strip()
            if not line:
                return

            if self._update_from_simple_status(line):
                return

            # Backward compatibility with JSON identify messages only.
            try:
                msg = json.loads(line)
            except Exception:
                if line.startswith("{") or '"type"' in line or '"mode"' in line:
                    return
                if line.startswith("DREAM CO2 multiplexer ready"):
                    self.append_log("Device ready")
                    return
                if line.startswith("ERROR") or line.startswith("FATAL"):
                    self.append_log(line)
                return

            if msg.get("type") == "identify":
                self.append_log("Connected: %s" % msg.get("device_name", "multiplexer"))
                return
            if msg.get("type") in ("error", "fatal", "pc_error"):
                self.append_log("ERROR: %s" % msg.get("message", line))
                return
        self.root.after(0, update)

    def start_cycle(self):
        if self.cycle_running:
            return
        try:
            self.settings["cycle_samples"] = self.parse_cycle_samples()
        except ValueError as exc:
            messagebox.showerror("Invalid cycle", str(exc))
            return
        self.save_current_settings()
        self.cycle_running = True
        self.cycle_thread = threading.Thread(target=self._cycle_loop, daemon=True)
        self.cycle_thread.start()
        self.append_log("Cycle started")

    def stop_cycle(self, log=True):
        was_running = self.cycle_running
        self.cycle_running = False
        if log and was_running:
            self.append_log("Cycle stopped")

    def _cycle_loop(self):
        samples = list(self.settings.get("cycle_samples", ["1", "2", "3", "1,2,3"]))
        while self.cycle_running:
            try:
                purge_s = float(self.purge_var.get())
                record_s = float(self.record_var.get())
            except ValueError:
                purge_s, record_s = 15.0, 30.0

            for sample in samples:
                if not self.cycle_running:
                    break
                try:
                    self.client.send({"cmd": "sample", "sample": sample})
                except Exception as exc:
                    self.root.after(0, lambda e=exc: self.append_log("Cycle send failed: %s" % e))
                    self.cycle_running = False
                    break

                self.root.after(0, lambda s=sample: self.append_log("%s: purge %.0f s" % (s, purge_s)))
                end_purge = time.time() + purge_s
                while self.cycle_running and time.time() < end_purge:
                    time.sleep(0.1)

                self.root.after(0, lambda s=sample: self.append_log("%s: record %.0f s" % (s, record_s)))
                end_record = time.time() + record_s
                while self.cycle_running and time.time() < end_record:
                    time.sleep(0.1)

    def on_close(self):
        self.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = MultiplexerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
