# DREAM_sensor_helper_PC.py
# PC helper for receiving, storing, and time-syncing DREAM QT Py environmental data
# Integrated with DREAM_BotFan_1 fan controller reporting fan speed % and RPM.

import os
import csv
import json
import math
import time
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


DEVICE_NAMES = [
    "DREAM_Sensors_1",
    "DREAM_Sensors_2",
    "DREAM_Sensors_3",
    "DREAM_Sensors_4",
    "DREAM_BotFan_1",
]


CSV_COLUMNS = [
    "pc_received_time",
    "device",
    "timestamp",
    "epoch",
    "time_label",
    "elapsed_s",
    "temp_c",
    "rh_percent",
    "co2_ppm",
    "pressure_hpa",
    "vpd_kpa",
    "air_velocity_ms",
    "par_raw",
    "par_umol_m2_s",
    "fan_speed_percent",
    "fan_rpm",
    "fan_dac_v",
]


MISSING_VALUE = "NAN"

NUMERIC_COLUMNS = [
    "epoch",
    "elapsed_s",
    "temp_c",
    "rh_percent",
    "co2_ppm",
    "pressure_hpa",
    "vpd_kpa",
    "air_velocity_ms",
    "par_raw",
    "par_umol_m2_s",
    "fan_speed_percent",
    "fan_rpm",
    "fan_dac_v",
]


def pc_time_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_string():
    return datetime.now().strftime("%Y-%m-%d")


def epoch_now():
    return int(time.time())


def pc_time_object():
    """Return PC local time as epoch plus separate fields.

    QT Py boards should use year/month/day/hour/minute/second fields for RTC
    setup to avoid UTC/daylight-saving offset mistakes.
    """
    now = datetime.now()
    return {
        "epoch": epoch_now(),
        "timestamp": pc_time_string(),
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second,
    }


def ensure_folder(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)


def safe_float(value):
    try:
        if value is None:
            return None

        if isinstance(value, str):
            text = value.strip()

            if text == "" or text.upper() in {"NAN", "NA", "NONE", "NULL", "--"}:
                return None

            number = float(text)
        else:
            number = float(value)

        if not math.isfinite(number):
            return None

        return number
    except Exception:
        return None


def format_decimal(value, digits):
    """Return a fixed-decimal string, or NAN when the value is invalid."""
    number = safe_float(value)

    if number is None:
        return MISSING_VALUE

    return f"{number:.{digits}f}"


def normalise_numeric_missing_values(row: dict[str, Any]) -> dict[str, Any]:
    """Use NAN consistently for missing or invalid numeric fields.

    Variables without a requested fixed precision keep their original value
    when valid, but become NAN when missing or non-numeric.
    """
    for key in NUMERIC_COLUMNS:
        value = row.get(key, "")

        if safe_float(value) is None:
            row[key] = MISSING_VALUE

    return row


def get_first_available(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return ""


def normalise_pressure_hpa(record: dict[str, Any]) -> Any:
    value = get_first_available(
        record,
        ["pressure_hpa", "pressure_hPa", "pressure", "pressure_mbar", "pressure_pa"],
    )

    p = safe_float(value)

    if p is None:
        return ""

    if 90000 <= p <= 110000:
        p = p / 100.0

    if 800 <= p <= 1200:
        return round(p, 2)

    return p


def normalise_record(record: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {key: "" for key in CSV_COLUMNS}
    row["pc_received_time"] = pc_time_string()

    for key, value in record.items():
        if key in row:
            row[key] = value

    row["pressure_hpa"] = normalise_pressure_hpa(record)

    if row["temp_c"] == "":
        row["temp_c"] = get_first_available(record, ["temp", "temperature", "temperature_c"])
    if row["rh_percent"] == "":
        row["rh_percent"] = get_first_available(record, ["rh", "RH", "humidity", "relative_humidity"])
    if row["co2_ppm"] == "":
        row["co2_ppm"] = get_first_available(record, ["co2", "CO2", "CO2_ppm"])
    if row["vpd_kpa"] == "":
        row["vpd_kpa"] = get_first_available(record, ["vpd", "VPD", "air_vpd_kpa"])
    if row["air_velocity_ms"] == "":
        row["air_velocity_ms"] = get_first_available(record, ["air_velocity", "air_speed", "airflow", "air_velocity_m_s"])
    if row["par_raw"] == "":
        row["par_raw"] = get_first_available(record, ["PAR_raw", "par_count", "par_counts"])
    if row["par_umol_m2_s"] == "":
        row["par_umol_m2_s"] = get_first_available(record, ["par", "PAR", "ppfd", "PAR_umol_m2_s"])

    if row["fan_speed_percent"] == "":
        row["fan_speed_percent"] = get_first_available(record, ["fan_percent", "speed_percent", "fan_speed", "fan_command_percent"])
    if row["fan_rpm"] == "":
        row["fan_rpm"] = get_first_available(record, ["rpm", "fanRPM", "fan_rpm_measured"])
    if row["fan_dac_v"] == "":
        row["fan_dac_v"] = get_first_available(record, ["dac_v", "fan_dac_voltage", "dac_voltage_v"])

    # Fixed precision for the variables requested for DREAM reporting/logging.
    # Other variables keep their received precision when valid.
    row["temp_c"] = format_decimal(row["temp_c"], 2)
    row["rh_percent"] = format_decimal(row["rh_percent"], 2)
    row["vpd_kpa"] = format_decimal(row["vpd_kpa"], 3)
    row["air_velocity_ms"] = format_decimal(row["air_velocity_ms"], 3)

    row = normalise_numeric_missing_values(row)

    return row


def record_unique_key(record):
    device = str(record.get("device", "unknown"))
    epoch = record.get("epoch", None)
    elapsed_s = record.get("elapsed_s", None)
    if epoch is not None and elapsed_s is not None:
        return f"{device}_{epoch}_{elapsed_s}"
    if epoch is not None:
        return f"{device}_{epoch}"
    timestamp = str(record.get("timestamp", ""))
    return f"{device}_{timestamp}_{elapsed_s}_{time.time()}"


class DREAMDataStore:
    def __init__(self, save_folder):
        # Store as an absolute path so records never depend on the
        # terminal's current working directory.
        self.save_folder = os.path.abspath(os.path.expanduser(str(save_folder)))
        ensure_folder(self.save_folder)
        self.lock = threading.Lock()
        self.records: list[dict[str, Any]] = []
        self.latest: dict[str, dict[str, Any] | None] = {}
        self.seen_keys: set[str] = set()
        for dev in DEVICE_NAMES:
            self.latest[dev] = None

    def csv_path(self):
        return os.path.join(self.save_folder, f"DREAM_env_log_{today_string()}.csv")

    def append_record(self, record):
        with self.lock:
            key = record_unique_key(record)
            if key in self.seen_keys:
                return False
            self.seen_keys.add(key)

            row: dict[str, Any] = normalise_record(record)
            self.records.append(row)

            device = str(row.get("device", ""))
            if device:
                self.latest[device] = row

            self._append_csv(row)
            return True

    def _append_csv(self, row):
        path = self.csv_path()
        need_header = (not os.path.exists(path)) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if need_header:
                writer.writeheader()
            writer.writerow(row)

    def get_records_since(self, start_epoch):
        with self.lock:
            result = []
            for row in self.records:
                ep = safe_float(row.get("epoch"))
                if ep is not None and ep >= start_epoch:
                    result.append(dict(row))
            return result

    def get_latest(self):
        with self.lock:
            result: dict[str, dict[str, Any] | None] = {}
            for dev in DEVICE_NAMES:
                row = self.latest.get(dev)
                result[dev] = None if row is None else row.copy()
            return result

    def summary_text(self):
        latest = self.get_latest()

        lines = [
            "=" * 100,
            f"PC time: {pc_time_string()}",
            f"CSV file: {self.csv_path()}",
        ]

        with self.lock:
            lines.append(f"Total records in memory: {len(self.records)}")

        for dev in DEVICE_NAMES:
            row = latest.get(dev)

            if row is None:
                lines.append(f"{dev}: no data")
                continue

            line = (
                f"{dev}: time={row.get('timestamp', '')}, "
                f"Temp={row.get('temp_c', '')} °C, "
                f"RH={row.get('rh_percent', '')} %, "
                f"CO2={row.get('co2_ppm', '')} ppm, "
                f"P={row.get('pressure_hpa', '')} hPa, "
                f"VPD={row.get('vpd_kpa', '')} kPa, "
                f"Air={row.get('air_velocity_ms', '')} m/s, "
                f"PAR={row.get('par_umol_m2_s', '')}"
            )

            if dev.startswith("DREAM_BotFan"):
                line += (
                    f", Fan speed={row.get('fan_speed_percent', '')} %, "
                    f"RPM={row.get('fan_rpm', '')}, "
                    f"DAC={row.get('fan_dac_v', '')} V"
                )

            lines.append(line)

        return "\n".join(lines)


def make_handler(data_store):
    class DREAMRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text, status=200):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/time"):
                self._send_json(pc_time_object())
                return

            if self.path.startswith("/latest"):
                self._send_json({
                    "now_epoch": epoch_now(),
                    "now_pc_time": pc_time_string(),
                    "latest": data_store.get_latest(),
                })
                return

            self._send_text("DREAM PC logger is running.", status=200)

        def do_POST(self):
            if not self.path.startswith("/data"):
                self._send_text("Not found", status=404)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                record = json.loads(body.decode("utf-8"))
                saved = data_store.append_record(record)
                self._send_json({
                    "ok": True,
                    "saved": saved,
                    "pc_epoch": epoch_now(),
                    "pc_time": pc_time_string(),
                })
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)

    return DREAMRequestHandler


class DREAMHTTPServer:
    def __init__(self, host, port, data_store):
        self.host = host
        self.port = port
        self.data_store = data_store
        self.httpd = HTTPServer((self.host, self.port), make_handler(self.data_store))

    def start_in_thread(self):
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self.httpd.shutdown()

