"""
DREAM_CO2_feedback_controller_v2_refined.py

PC-controlled CO2 feedback loop for DREAM chamber.

Features:
- Reads LI-850 XML serial output.
- Uses LI-850 data/co2, not raw/co2.
- Controls Bronkhorst F-201CV CO2 MFC via FLOW-BUS/RS485 using bronkhorst-propar.
- Adds MFC rate limiting.
- Adds minimum MFC ON/OFF time.
- Adds feedforward + feedback MFC command.
- Adds time-windowed average MFC CO2 supply.
- Adds direct MFC-only assimilation estimate.
- Adds smoothed MFC-only assimilation estimate.
- Adds regression-based dCO2/dt over configurable windows.
- Logs comparison between:
    1. simple dC/dt method
    2. regression dC/dt method
    3. MFC-only CO2 supply method
    4. storage-corrected A_canopy estimates
    5. smoothed A_canopy estimates

Install:
    python -m pip install pyserial bronkhorst-propar

Run:
    python DREAM_CO2_feedback_controller_v2_refined.py
"""

import argparse
import csv
import json
import os
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import serial

try:
    import propar
except ImportError:
    propar = None


# ============================================================
# USER SETTINGS
# ============================================================

# -----------------------------
# Serial ports
# -----------------------------
LI850_PORT = "COM11"          # Change to your LI-850 COM port
BRONKHORST_PORT = "COM5"      # Change to your Bronkhorst / FlowSuite COM port
BRONKHORST_ADDRESS = 6        # CO2 regulation MFC FLOW-BUS address

# -----------------------------
# Bronkhorst communication
# -----------------------------
BRONKHORST_BAUDRATE = 38400

# Full-scale flow of CO2 MFC in normal mL/min.
MFC_FULL_SCALE_MLN_MIN = 200.0

# Gas composition:
# Pure CO2 cylinder: 1.0
# 1% CO2 stock gas: 0.01
CO2_FRACTION_IN_MFC_GAS = 1.0

# -----------------------------
# Chamber physical parameters
# -----------------------------
CHAMBER_VOLUME_M3 = 2.33

# Use chamber air temperature, not LI-850 cell temperature.
CHAMBER_AIR_TEMP_C = 18.0

# Optional leaf area.
# Set to a number, e.g. 1.25, if you want area-based assimilation.
# Leave as None if unknown.
LEAF_AREA_M2 = None

# -----------------------------
# CO2 control settings
# -----------------------------
TARGET_CO2_PPM = 452.0
DEADBAND_PPM = 2.0

# Proportional feedback:
# feedback_flow = KP * CO2 error
# Safer first-test value. Increase later only if response is too slow.
KP_MLN_MIN_PER_PPM = 1.0

# Optional integral feedback.
# Start with 0.0. Increase very cautiously only if steady-state CO2 is biased low.
KI_MLN_MIN_PER_PPM_S = 0.0

# Safety cap for normal operation.
# Safer first-test value. Increase later only if the chamber cannot recover fast enough.
MAX_CO2_FLOW_MLN_MIN = 50.0

# -----------------------------
# Manual MFC override
# -----------------------------
# When enabled, the controller ignores feedback/feedforward and commands this
# MFC flow directly. The high-CO2 safety cutoff still forces the MFC to zero.
MANUAL_MFC_OVERRIDE_ENABLED = False
MANUAL_MFC_FLOW_MLN_MIN = 0.0

# Safety cutoff, not normal control threshold.
# Must be > TARGET_CO2_PPM + DEADBAND_PPM.
HIGH_CO2_CUTOFF_PPM = TARGET_CO2_PPM + 5.0

# Control timing
CONTROL_INTERVAL_S = 5.0
CO2_AVERAGE_WINDOW_S = 20.0

# -----------------------------
# MFC command smoothing / stability
# -----------------------------
MAX_MFC_STEP_MLN_MIN = 3.0

MIN_MFC_ON_TIME_S = 20.0
MIN_MFC_OFF_TIME_S = 20.0

# Flow below this value is treated as zero.
MIN_EFFECTIVE_MFC_FLOW_MLN_MIN = 0.5

# Estimated delay between MFC injection and LI-850 detection.
# Determine this experimentally by a CO2 pulse test.
MFC_TO_LI850_LAG_S = 10.0

# -----------------------------
# Feedforward + feedback control
# -----------------------------
USE_FEEDFORWARD_CONTROL = True

# Window used to estimate recent plant CO2 uptake for feedforward.
FEEDFORWARD_A_WINDOW_S = 180.0

# Maximum feedforward contribution.
MAX_FEEDFORWARD_FLOW_MLN_MIN = 15.0

# Exponential smoothing for feedforward estimate.
# 0.2 = slow/stable, 0.5 = faster/noisier.
FEEDFORWARD_SMOOTHING_ALPHA = 0.2

# When CO2 is within deadband but still below target,
# only use part of the feedforward flow.
FEEDFORWARD_WITHIN_DEADBAND_FACTOR = 0.3

# -----------------------------
# Assimilation calculation from average MFC CO2 supply
# -----------------------------
MFC_SUPPLY_AVERAGE_WINDOW_S = 180.0

# Time window for smoothing final A_canopy values.
A_SMOOTHING_WINDOW_S = 180.0

# -----------------------------
# dC/dt calculation settings
# -----------------------------

# Simple dC/dt calculation window.
# This uses oldest-to-newest difference over the window.
DCDT_WINDOW_S = 60.0

# Regression-based dC/dt calculation.
USE_REGRESSION_DCDT = True
REGRESSION_DCDT_WINDOW_S = 180.0
REGRESSION_DCDT_SHORT_WINDOW_S = 60.0

# Smoothing window for A calculated with regression dC/dt.
A_REGRESSION_SMOOTHING_WINDOW_S = 180.0

# -----------------------------
# LI-850 serial settings
# -----------------------------
LI850_BAUDRATE = 9600
LI850_TIMEOUT_S = 1.0

# -----------------------------
# Log file
# -----------------------------
LOG_FOLDER = "DREAM_CO2_logs"
LOG_PREFIX = "DREAM_CO2_feedback_v2_refined"

# -----------------------------
# Safety behaviour
# -----------------------------
SET_MFC_ZERO_ON_EXIT = True
STOP_IF_NO_LI850_DATA_FOR_S = 30.0

# Start with True for software testing.
# Set to False only when LI-850 and MFC connection are confirmed.
DRY_RUN_NO_MFC_WRITE = False

# -----------------------------
# Pre-check / safety settings
# -----------------------------
RUN_PRECHECK_BEFORE_CONTROL = False
CHECK_MFC_CONNECTION_BEFORE_CONTROL = False

PRECHECK_MIN_CO2_PPM = 50.0
PRECHECK_MAX_CO2_PPM = 2000.0
PRECHECK_MIN_PRESSURE_KPA = 80.0
PRECHECK_MAX_PRESSURE_KPA = 120.0
PRECHECK_MIN_H2O_MMOL_MOL = 0.0
PRECHECK_MAX_H2O_MMOL_MOL = 40.0

PRECHECK_LI850_VALID_READINGS = 5
PRECHECK_TIMEOUT_S = 20.0

RUN_SMALL_MFC_SETPOINT_TEST = True
MFC_TEST_FLOW_MLN_MIN = 5.0
MFC_TEST_HOLD_S = 3.0
MFC_ZERO_TOLERANCE_MLN_MIN = 0.5


# ============================================================
# RUNTIME CONFIGURATION
# ============================================================

DEFAULT_CONFIG_PATH = "DREAM_CO2_feedback_settings.json"
CONFIG_RELOAD_INTERVAL_S = 2.0

# live=True means the value can be changed while the controller is running.
# live=False means the value is read at startup only; restart the controller after editing.
CONFIG_PARAMS = {
    # Serial / hardware startup settings
    "LI850_PORT": {"type": "str", "live": False},
    "BRONKHORST_PORT": {"type": "str", "live": False},
    "BRONKHORST_ADDRESS": {"type": "int", "min": 1, "max": 255, "live": False},
    "BRONKHORST_BAUDRATE": {"type": "int", "min": 1200, "max": 1000000, "live": False},
    "MFC_FULL_SCALE_MLN_MIN": {"type": "float", "min": 1.0, "max": 5000.0, "live": False},
    "LI850_BAUDRATE": {"type": "int", "min": 1200, "max": 115200, "live": False},
    "LI850_TIMEOUT_S": {"type": "float", "min": 0.1, "max": 10.0, "live": False},
    "LOG_FOLDER": {"type": "str", "live": False},
    "LOG_PREFIX": {"type": "str", "live": False},

    # Physical / gas settings
    "CO2_FRACTION_IN_MFC_GAS": {"type": "float", "min": 0.0001, "max": 1.0, "live": True},
    "CHAMBER_VOLUME_M3": {"type": "float", "min": 0.01, "max": 100.0, "live": True},
    "CHAMBER_AIR_TEMP_C": {"type": "float", "min": -20.0, "max": 60.0, "live": True},
    "LEAF_AREA_M2": {"type": "optional_float", "min": 0.0001, "max": 1000.0, "live": True},

    # CO2 feedback settings
    "TARGET_CO2_PPM": {"type": "float", "min": 50.0, "max": 3000.0, "live": True},
    "DEADBAND_PPM": {"type": "float", "min": 0.1, "max": 100.0, "live": True},
    "KP_MLN_MIN_PER_PPM": {"type": "float", "min": 0.0, "max": 100.0, "live": True},
    "KI_MLN_MIN_PER_PPM_S": {"type": "float", "min": 0.0, "max": 10.0, "live": True},
    "MAX_CO2_FLOW_MLN_MIN": {"type": "float", "min": 0.0, "max": 5000.0, "live": True},
    "MANUAL_MFC_OVERRIDE_ENABLED": {"type": "bool", "live": True},
    "MANUAL_MFC_FLOW_MLN_MIN": {"type": "float", "min": 0.0, "max": 5000.0, "live": True},
    "HIGH_CO2_CUTOFF_PPM": {"type": "float", "min": 50.0, "max": 5000.0, "live": True},
    "CONTROL_INTERVAL_S": {"type": "float", "min": 1.0, "max": 300.0, "live": True},
    "CO2_AVERAGE_WINDOW_S": {"type": "float", "min": 1.0, "max": 3600.0, "live": True},

    # MFC command smoothing / stability
    "MAX_MFC_STEP_MLN_MIN": {"type": "float", "min": 0.1, "max": 5000.0, "live": True},
    "MIN_MFC_ON_TIME_S": {"type": "float", "min": 0.0, "max": 3600.0, "live": True},
    "MIN_MFC_OFF_TIME_S": {"type": "float", "min": 0.0, "max": 3600.0, "live": True},
    "MIN_EFFECTIVE_MFC_FLOW_MLN_MIN": {"type": "float", "min": 0.0, "max": 100.0, "live": True},
    "MFC_TO_LI850_LAG_S": {"type": "float", "min": 0.0, "max": 600.0, "live": True},

    # Feedforward settings
    "USE_FEEDFORWARD_CONTROL": {"type": "bool", "live": True},
    "FEEDFORWARD_A_WINDOW_S": {"type": "float", "min": 1.0, "max": 7200.0, "live": True},
    "MAX_FEEDFORWARD_FLOW_MLN_MIN": {"type": "float", "min": 0.0, "max": 5000.0, "live": True},
    "FEEDFORWARD_SMOOTHING_ALPHA": {"type": "float", "min": 0.0, "max": 1.0, "live": True},
    "FEEDFORWARD_WITHIN_DEADBAND_FACTOR": {"type": "float", "min": 0.0, "max": 1.0, "live": True},

    # Assimilation calculation settings
    "MFC_SUPPLY_AVERAGE_WINDOW_S": {"type": "float", "min": 1.0, "max": 7200.0, "live": True},
    "A_SMOOTHING_WINDOW_S": {"type": "float", "min": 1.0, "max": 7200.0, "live": True},
    "DCDT_WINDOW_S": {"type": "float", "min": 1.0, "max": 7200.0, "live": True},
    "USE_REGRESSION_DCDT": {"type": "bool", "live": True},
    "REGRESSION_DCDT_WINDOW_S": {"type": "float", "min": 1.0, "max": 7200.0, "live": True},
    "REGRESSION_DCDT_SHORT_WINDOW_S": {"type": "float", "min": 1.0, "max": 7200.0, "live": True},
    "A_REGRESSION_SMOOTHING_WINDOW_S": {"type": "float", "min": 1.0, "max": 7200.0, "live": True},

    # Safety / test settings
    "SET_MFC_ZERO_ON_EXIT": {"type": "bool", "live": True},
    "STOP_IF_NO_LI850_DATA_FOR_S": {"type": "float", "min": 1.0, "max": 3600.0, "live": True},
    "DRY_RUN_NO_MFC_WRITE": {"type": "bool", "live": False},
    "RUN_PRECHECK_BEFORE_CONTROL": {"type": "bool", "live": False},
    "CHECK_MFC_CONNECTION_BEFORE_CONTROL": {"type": "bool", "live": False},
    "RUN_SMALL_MFC_SETPOINT_TEST": {"type": "bool", "live": False},
    "MFC_TEST_FLOW_MLN_MIN": {"type": "float", "min": 0.0, "max": 1000.0, "live": False},
    "MFC_TEST_HOLD_S": {"type": "float", "min": 0.0, "max": 60.0, "live": False},
    "MFC_ZERO_TOLERANCE_MLN_MIN": {"type": "float", "min": 0.0, "max": 100.0, "live": False},
}


def _coerce_config_value(name: str, value, spec: dict):
    value_type = spec.get("type", "str")

    if value_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    if value_type == "int":
        coerced = int(float(value))
    elif value_type == "float":
        coerced = float(value)
    elif value_type == "optional_float":
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null", "na"}:
            return None
        coerced = float(value)
    else:
        return str(value)

    if "min" in spec and coerced < spec["min"]:
        coerced = spec["min"]
    if "max" in spec and coerced > spec["max"]:
        coerced = spec["max"]

    return coerced


def current_config_dict() -> dict:
    return {name: globals().get(name) for name in CONFIG_PARAMS}


def write_default_config(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = current_config_dict()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[CONFIG WARNING] Could not read config file {path}: {exc}")
        return {}


def apply_config_dict(config: dict, startup: bool = False):
    changed = []

    for name, spec in CONFIG_PARAMS.items():
        if name not in config:
            continue
        if not startup and not spec.get("live", False):
            continue

        try:
            new_value = _coerce_config_value(name, config[name], spec)
        except Exception as exc:
            print(f"[CONFIG WARNING] Ignoring invalid {name}={config[name]!r}: {exc}")
            continue

        old_value = globals().get(name)
        if old_value != new_value:
            globals()[name] = new_value
            changed.append(name)

    # Safety consistency checks after applying settings.
    if globals().get("HIGH_CO2_CUTOFF_PPM") <= globals().get("TARGET_CO2_PPM") + globals().get("DEADBAND_PPM"):
        globals()["HIGH_CO2_CUTOFF_PPM"] = globals().get("TARGET_CO2_PPM") + globals().get("DEADBAND_PPM") + 1.0
        changed.append("HIGH_CO2_CUTOFF_PPM(auto)")

    if globals().get("MAX_CO2_FLOW_MLN_MIN") > globals().get("MFC_FULL_SCALE_MLN_MIN"):
        globals()["MAX_CO2_FLOW_MLN_MIN"] = globals().get("MFC_FULL_SCALE_MLN_MIN")
        changed.append("MAX_CO2_FLOW_MLN_MIN(auto)")

    if changed:
        mode = "startup" if startup else "live"
        print(f"[CONFIG] Applied {mode} config changes: {', '.join(changed)}")


def reload_config_if_changed(config_path: Path, last_mtime: float | None) -> float | None:
    try:
        mtime = config_path.stat().st_mtime
    except FileNotFoundError:
        return last_mtime

    if last_mtime is None or mtime > last_mtime:
        config = load_config_file(config_path)
        apply_config_dict(config, startup=False)
        return mtime

    return last_mtime


def parse_runtime_args():
    parser = argparse.ArgumentParser(description="DREAM CO2 feedback controller with live JSON settings.")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to JSON settings file. Live-tunable settings are reloaded while running.",
    )
    parser.add_argument(
        "--write-default-config",
        action="store_true",
        help="Write a default JSON settings file and exit.",
    )
    return parser.parse_args()


# ============================================================
# CONSTANTS
# ============================================================

R_GAS = 8.314462618
DEFAULT_PRESSURE_PA = 101325.0
NORMAL_MOLAR_VOLUME_L_PER_MOL = 22.414


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class LI850Reading:
    timestamp: float
    co2_ppm: float
    h2o_mmol_mol: float | None = None
    celltemp_c: float | None = None
    cellpres_kpa: float | None = None
    flowrate: float | None = None
    raw_xml: str = ""


@dataclass
class MFCRecord:
    timestamp: float
    setpoint_mln_min: float
    actual_mln_min: float | None
    flow_used_mln_min: float
    co2_supply_umol_s: float


@dataclass
class ARecord:
    timestamp: float
    A_inst_umol_s: float | None
    A_MFC_only_umol_s: float | None
    A_from_avg_supply_umol_s: float | None
    A_regression_umol_s: float | None
    A_regression_short_umol_s: float | None


# ============================================================
# LI-850 READER
# ============================================================

class LI850Reader:
    """
    Reads LI-850 serial XML.

    Uses:
        data/co2
        data/h2o
        data/cellpres
        data/celltemp
        data/flowrate

    Ignores:
        data/raw/co2
    """

    def __init__(self, port: str):
        self.ser = serial.Serial(
            port=port,
            baudrate=LI850_BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=LI850_TIMEOUT_S,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    @staticmethod
    def _get_float(parent, tag: str) -> float | None:
        elem = parent.find(tag)
        if elem is None or elem.text is None:
            return None

        try:
            return float(elem.text.strip())
        except ValueError:
            return None

    def read_one(self) -> LI850Reading | None:
        try:
            line_bytes = self.ser.readline()
        except Exception as exc:
            print(f"[LI850] Serial read error: {exc}")
            return None

        if not line_bytes:
            return None

        text = line_bytes.decode(errors="replace").strip()

        if not text:
            return None

        if "<li850>" not in text or "</li850>" not in text:
            return None

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None

        data = root.find("data")
        if data is None:
            return None

        co2 = self._get_float(data, "co2")
        if co2 is None:
            return None

        return LI850Reading(
            timestamp=time.time(),
            co2_ppm=co2,
            h2o_mmol_mol=self._get_float(data, "h2o"),
            celltemp_c=self._get_float(data, "celltemp"),
            cellpres_kpa=self._get_float(data, "cellpres"),
            flowrate=self._get_float(data, "flowrate"),
            raw_xml=text,
        )


# ============================================================
# BRONKHORST MFC CONTROL
# ============================================================

class BronkhorstMFC:
    """
    Controls Bronkhorst MFC using bronkhorst-propar.

    propar setpoint and measure use:
        0     = 0% full scale
        32000 = 100% full scale

    This class converts between normal mL/min and the internal 0-32000 scale.
    """

    def __init__(self, port: str, address: int, full_scale_mln_min: float):
        self.port = port
        self.address = address
        self.full_scale = float(full_scale_mln_min)

        if propar is None:
            raise RuntimeError(
                "bronkhorst-propar is not installed. Run: python -m pip install bronkhorst-propar"
            )

        self.instrument = propar.instrument(
            port,
            address=address,
            baudrate=BRONKHORST_BAUDRATE,
        )

    def mln_min_to_raw(self, flow_mln_min: float) -> int:
        flow = max(0.0, min(float(flow_mln_min), self.full_scale))
        raw = round(32000.0 * flow / self.full_scale)
        return int(max(0, min(raw, 32000)))

    def raw_to_mln_min(self, raw_value) -> float | None:
        if raw_value is None:
            return None

        try:
            return float(raw_value) * self.full_scale / 32000.0
        except Exception:
            return None

    def set_flow(self, flow_mln_min: float) -> bool:
        raw = self.mln_min_to_raw(flow_mln_min)

        if DRY_RUN_NO_MFC_WRITE:
            print(f"[DRY RUN] Would set MFC to {flow_mln_min:.3f} mln/min, raw={raw}")
            return True

        try:
            self.instrument.setpoint = raw
            return True
        except Exception as exc:
            print(f"[MFC] Failed to set flow: {exc}")
            return False

    def read_actual_flow(self) -> float | None:
        if DRY_RUN_NO_MFC_WRITE:
            return None

        try:
            raw = self.instrument.measure
            return self.raw_to_mln_min(raw)
        except Exception as exc:
            print(f"[MFC] Failed to read actual flow: {exc}")
            return None

    def close_zero(self):
        try:
            self.set_flow(0.0)
        except Exception:
            pass


# ============================================================
# CALCULATION UTILITIES
# ============================================================

def chamber_air_moles(
    volume_m3: float,
    pressure_kpa: float | None,
    chamber_air_temp_c: float,
) -> float:
    pressure_pa = DEFAULT_PRESSURE_PA if pressure_kpa is None else pressure_kpa * 1000.0
    temperature_k = chamber_air_temp_c + 273.15
    return pressure_pa * volume_m3 / (R_GAS * temperature_k)


def normal_flow_to_co2_umol_s(
    flow_mln_min: float,
    co2_fraction: float = 1.0,
) -> float:
    """
    Converts normal mL/min gas flow to umol CO2/s.
    """
    gas_l_n_per_min = flow_mln_min / 1000.0
    co2_mol_per_min = gas_l_n_per_min * co2_fraction / NORMAL_MOLAR_VOLUME_L_PER_MOL
    return co2_mol_per_min * 1e6 / 60.0


def co2_umol_s_to_normal_flow_mln_min(
    co2_umol_s: float,
    co2_fraction: float = 1.0,
) -> float:
    """
    Converts umol CO2/s to normal mL/min MFC gas flow.
    """
    if co2_fraction <= 0:
        return 0.0

    co2_mol_min = co2_umol_s / 1e6 * 60.0
    gas_l_n_min = co2_mol_min * NORMAL_MOLAR_VOLUME_L_PER_MOL / co2_fraction
    return gas_l_n_min * 1000.0


def latest_reading(history: deque) -> LI850Reading | None:
    if not history:
        return None
    return history[-1]


def moving_average_co2(history: deque, window_s: float) -> float | None:
    now = time.time()
    values = [r.co2_ppm for r in history if now - r.timestamp <= window_s]

    if not values:
        return None

    return sum(values) / len(values)


def moving_average_mfc_supply(
    mfc_history: deque,
    window_s: float,
    lag_s: float = 0.0,
) -> float | None:
    """
    Average MFC CO2 supply over a lag-corrected time window.

    At current LI-850 time t, compare with MFC supply around t - lag_s.
    """
    now = time.time()
    end_t = now - lag_s
    start_t = end_t - window_s

    values = [
        r.co2_supply_umol_s
        for r in mfc_history
        if start_t <= r.timestamp <= end_t
    ]

    if not values:
        return None

    return sum(values) / len(values)


def moving_average_A(
    a_history: deque,
    field_name: str,
    window_s: float,
) -> float | None:
    now = time.time()
    values = []

    for r in a_history:
        if now - r.timestamp <= window_s:
            value = getattr(r, field_name)
            if value is not None:
                values.append(value)

    if not values:
        return None

    return sum(values) / len(values)


def linear_slope(x, y) -> float | None:
    """
    Ordinary least-squares slope dy/dx.
    """
    n = len(x)
    if n < 2:
        return None

    x_mean = sum(x) / n
    y_mean = sum(y) / n

    sxx = sum((xi - x_mean) ** 2 for xi in x)
    if sxx <= 0:
        return None

    sxy = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    return sxy / sxx


def calculate_dcdt_regression_ppm_s(history: deque, window_s: float) -> float | None:
    """
    Regression-based dCO2/dt in ppm/s.
    More stable than point-to-point differences.
    """
    now = time.time()
    recent = [r for r in history if now - r.timestamp <= window_s]

    if len(recent) < 5:
        return None

    t0 = recent[0].timestamp
    x = [r.timestamp - t0 for r in recent]
    y = [r.co2_ppm for r in recent]

    return linear_slope(x, y)


def calculate_dcdt_simple_ppm_s(history: deque, window_s: float) -> float | None:
    """
    Simple dCO2/dt:
    newest minus oldest value inside the selected time window.
    """
    now = time.time()
    recent = [r for r in history if now - r.timestamp <= window_s]

    if len(recent) < 2:
        return None

    old = recent[0]
    new = recent[-1]
    dt = new.timestamp - old.timestamp

    if dt <= 0:
        return None

    return (new.co2_ppm - old.co2_ppm) / dt


def safe_fmt(value, digits=3):
    if value is None:
        return "NA"
    try:
        return f"{value:.{digits}f}"
    except Exception:
        return "NA"


# ============================================================
# MFC CONTROL LOGIC
# ============================================================

def apply_rate_limit(
    requested_cmd: float,
    previous_cmd: float,
) -> float:
    lower = previous_cmd - MAX_MFC_STEP_MLN_MIN
    upper = previous_cmd + MAX_MFC_STEP_MLN_MIN
    return max(lower, min(upper, requested_cmd))


def apply_min_on_off_time(
    requested_cmd: float,
    previous_cmd: float,
    now: float,
    last_on_time: float | None,
    last_off_time: float | None,
):
    """
    Prevent rapid MFC ON/OFF switching.
    """
    was_on = previous_cmd > MIN_EFFECTIVE_MFC_FLOW_MLN_MIN
    wants_on = requested_cmd > MIN_EFFECTIVE_MFC_FLOW_MLN_MIN

    # Trying to switch OFF too soon
    if was_on and not wants_on:
        if last_on_time is not None and now - last_on_time < MIN_MFC_ON_TIME_S:
            requested_cmd = previous_cmd
            wants_on = True

    # Trying to switch ON too soon
    elif not was_on and wants_on:
        if last_off_time is not None and now - last_off_time < MIN_MFC_OFF_TIME_S:
            requested_cmd = 0.0
            wants_on = False

    is_on = requested_cmd > MIN_EFFECTIVE_MFC_FLOW_MLN_MIN

    if not was_on and is_on:
        last_on_time = now

    if was_on and not is_on:
        last_off_time = now

    return requested_cmd, last_on_time, last_off_time


def calculate_feedback_setpoint(
    co2_avg_ppm: float | None,
    previous_cmd: float,
    integral_error: float,
    dt_s: float,
    feedforward_flow_mln_min: float,
    dcdt_regression_ppm_s: float | None,
):
    """
    Calculates the raw MFC setpoint before rate limiting and ON/OFF lockout.

    Logic:
    - If CO2 >= safety cutoff: zero.
    - If CO2 > target + deadband: zero.
    - If CO2 is within deadband but below target: optional partial feedforward.
    - If CO2 < target - deadband: feedforward + proportional feedback.
    """

    if co2_avg_ppm is None:
        return 0.0, 0.0, "no CO2 average; MFC zero"

    lower_limit = TARGET_CO2_PPM - DEADBAND_PPM
    upper_limit = TARGET_CO2_PPM + DEADBAND_PPM

    # Safety cutoff
    if co2_avg_ppm >= HIGH_CO2_CUTOFF_PPM:
        return 0.0, 0.0, "high CO2 safety cutoff; MFC zero"

    # Above upper deadband: no CO2 addition
    if co2_avg_ppm > upper_limit:
        return 0.0, 0.0, "above upper deadband; MFC zero"

    # Above target but not above upper deadband:
    # avoid feedforward because injected CO2 may still be mixing.
    if co2_avg_ppm >= TARGET_CO2_PPM:
        return 0.0, 0.0, "at/above target within deadband; MFC zero"

    # Within lower part of deadband:
    # allow small feedforward only if CO2 is not already rising.
    if lower_limit <= co2_avg_ppm < TARGET_CO2_PPM:
        if not USE_FEEDFORWARD_CONTROL:
            return 0.0, integral_error, "within lower deadband; MFC zero"

        if dcdt_regression_ppm_s is not None and dcdt_regression_ppm_s > 0:
            return 0.0, integral_error, "within deadband and CO2 rising; MFC zero"

        cmd = feedforward_flow_mln_min * FEEDFORWARD_WITHIN_DEADBAND_FACTOR
        cmd = min(cmd, MAX_CO2_FLOW_MLN_MIN)

        if cmd < MIN_EFFECTIVE_MFC_FLOW_MLN_MIN:
            cmd = 0.0

        return cmd, integral_error, "within lower deadband; partial feedforward"

    # Below lower deadband: feedforward + feedback
    error = TARGET_CO2_PPM - co2_avg_ppm

    integral_error += error * dt_s
    feedback_flow = KP_MLN_MIN_PER_PPM * error
    integral_flow = KI_MLN_MIN_PER_PPM_S * integral_error

    if USE_FEEDFORWARD_CONTROL:
        cmd = feedforward_flow_mln_min + feedback_flow + integral_flow
    else:
        cmd = feedback_flow + integral_flow

    cmd = max(0.0, min(cmd, MAX_CO2_FLOW_MLN_MIN))

    if cmd < MIN_EFFECTIVE_MFC_FLOW_MLN_MIN:
        cmd = 0.0

    return cmd, integral_error, "below deadband; feedforward + feedback"


# ============================================================
# PRE-CHECK FUNCTIONS
# ============================================================

def check_bronkhorst_mfc_connection(mfc: BronkhorstMFC) -> bool:
    print("\n[MFC CHECK] Checking Bronkhorst CO2 MFC connection...")
    print(f"[MFC CHECK] Port:       {BRONKHORST_PORT}")
    print(f"[MFC CHECK] Address:    {BRONKHORST_ADDRESS}")
    print(f"[MFC CHECK] Full scale: {MFC_FULL_SCALE_MLN_MIN:.3f} mln/min")

    print("[MFC CHECK] Sending zero setpoint...")
    ok = mfc.set_flow(0.0)

    if not ok:
        print("[MFC CHECK ERROR] Failed to send zero setpoint.")
        return False

    time.sleep(1.0)

    if DRY_RUN_NO_MFC_WRITE:
        print("[MFC CHECK] Dry-run mode: readback skipped.")
    else:
        actual_zero = mfc.read_actual_flow()

        if actual_zero is None:
            print("[MFC CHECK ERROR] Could not read MFC actual flow.")
            return False

        print(f"[MFC CHECK] Actual flow at zero setpoint: {actual_zero:.4f} mln/min")

        if abs(actual_zero) > MFC_ZERO_TOLERANCE_MLN_MIN:
            print("[MFC CHECK ERROR] Actual flow is not close to zero.")
            return False

    if RUN_SMALL_MFC_SETPOINT_TEST and not DRY_RUN_NO_MFC_WRITE:
        print(
            f"[MFC CHECK] Small setpoint test: "
            f"{MFC_TEST_FLOW_MLN_MIN:.3f} mln/min for {MFC_TEST_HOLD_S:.1f} s"
        )

        ok = mfc.set_flow(MFC_TEST_FLOW_MLN_MIN)
        if not ok:
            print("[MFC CHECK ERROR] Failed to send small setpoint.")
            mfc.set_flow(0.0)
            return False

        time.sleep(MFC_TEST_HOLD_S)

        actual_test = mfc.read_actual_flow()
        print(f"[MFC CHECK] Actual flow at test setpoint: {actual_test}")

        print("[MFC CHECK] Returning MFC to zero...")
        mfc.set_flow(0.0)
        time.sleep(1.0)

        actual_after = mfc.read_actual_flow()
        print(f"[MFC CHECK] Actual flow after zero: {actual_after}")

        if actual_test is None:
            print("[MFC CHECK ERROR] Could not read actual flow during test.")
            return False

        if actual_after is not None and abs(actual_after) > MFC_ZERO_TOLERANCE_MLN_MIN:
            print("[MFC CHECK ERROR] MFC did not return close to zero.")
            return False

    print("[MFC CHECK] Passed.\n")
    return True


def run_precheck(li850: LI850Reader, mfc: BronkhorstMFC) -> bool:
    print("\n[PRECHECK] Starting safety and connection checks...")

    print("[PRECHECK] Setting MFC to zero...")
    if not mfc.set_flow(0.0):
        print("[PRECHECK ERROR] Could not set MFC to zero.")
        return False

    time.sleep(1.0)

    valid_readings = []
    start = time.time()

    print("[PRECHECK] Reading LI-850...")

    while time.time() - start < PRECHECK_TIMEOUT_S:
        r = li850.read_one()

        if r is None:
            time.sleep(0.2)
            continue

        print(
            f"[PRECHECK] CO2={r.co2_ppm:.2f} ppm, "
            f"H2O={r.h2o_mmol_mol}, "
            f"Pcell={r.cellpres_kpa}, "
            f"Tcell={r.celltemp_c}, "
            f"flowrate={r.flowrate}"
        )

        if not (PRECHECK_MIN_CO2_PPM <= r.co2_ppm <= PRECHECK_MAX_CO2_PPM):
            print("[PRECHECK ERROR] LI-850 CO2 outside expected range.")
            return False

        if r.cellpres_kpa is not None:
            if not (PRECHECK_MIN_PRESSURE_KPA <= r.cellpres_kpa <= PRECHECK_MAX_PRESSURE_KPA):
                print("[PRECHECK ERROR] LI-850 pressure outside expected range.")
                return False

        if r.h2o_mmol_mol is not None:
            if not (PRECHECK_MIN_H2O_MMOL_MOL <= r.h2o_mmol_mol <= PRECHECK_MAX_H2O_MMOL_MOL):
                print("[PRECHECK ERROR] LI-850 H2O outside expected range.")
                return False

        valid_readings.append(r)

        if len(valid_readings) >= PRECHECK_LI850_VALID_READINGS:
            break

    if len(valid_readings) < PRECHECK_LI850_VALID_READINGS:
        print("[PRECHECK ERROR] Not enough valid LI-850 readings.")
        return False

    co2_mean = sum(r.co2_ppm for r in valid_readings) / len(valid_readings)
    print(f"[PRECHECK] Mean CO2 = {co2_mean:.2f} ppm")

    if co2_mean >= TARGET_CO2_PPM:
        print("[PRECHECK] CO2 already above target. MFC will remain zero.")
        mfc.set_flow(0.0)

    print("[PRECHECK] Passed.\n")
    return True


# ============================================================
# LOGGING
# ============================================================

def make_log_path() -> str:
    os.makedirs(LOG_FOLDER, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(LOG_FOLDER, f"{LOG_PREFIX}_{stamp}.csv")


def open_log_file(path: str):
    f = open(path, "w", newline="", encoding="utf-8")
    writer = csv.writer(f)

    writer.writerow([
        "timestamp",
        "elapsed_s",

        "li850_co2_ppm",
        "co2_avg_ppm",
        "target_co2_ppm",
        "co2_error_ppm",

        "li850_h2o_mmol_mol",
        "li850_cellpres_kpa",
        "li850_celltemp_c",
        "li850_flowrate",

        "chamber_air_temp_c",
        "chamber_air_mol",

        "mfc_raw_requested_mln_min",
        "mfc_rate_limited_mln_min",
        "mfc_setpoint_mln_min",
        "mfc_actual_mln_min",
        "mfc_flow_used_mln_min",
        "manual_mfc_override_enabled",
        "manual_mfc_flow_mln_min",

        "co2_supply_inst_umol_s",
        "co2_supply_avg_umol_s",
        "A_MFC_only_umol_s",
        "A_MFC_only_smoothed_umol_s",
        "co2_supplied_umol_step",
        "co2_supplied_mmol_total",

        "feedforward_flow_mln_min",
        "raw_feedforward_flow_mln_min",

        "dCdt_simple_ppm_s",
        "dCdt_regression_ppm_s",
        "dCdt_regression_short_ppm_s",

        "storage_simple_umol_s",
        "storage_regression_umol_s",
        "storage_regression_short_umol_s",

        "A_inst_umol_s",
        "A_from_avg_supply_umol_s",
        "A_regression_umol_s",
        "A_regression_short_umol_s",

        "A_smoothed_umol_s",
        "A_regression_smoothed_umol_s",
        "NE_regression_umol_s",

        "A_inst_umol_m2_s",
        "A_from_avg_supply_umol_m2_s",
        "A_MFC_only_umol_m2_s",
        "A_regression_umol_m2_s",
        "A_smoothed_umol_m2_s",

        "control_note",
    ])

    return f, writer


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    args = parse_runtime_args()
    config_path = Path(args.config)

    if args.write_default_config:
        write_default_config(config_path)
        print(f"[CONFIG] Wrote default config to: {config_path}")
        return

    if not config_path.exists():
        write_default_config(config_path)
        print(f"[CONFIG] Created default config: {config_path}")

    apply_config_dict(load_config_file(config_path), startup=True)
    last_config_mtime = config_path.stat().st_mtime if config_path.exists() else None

    print("\nDREAM CO2 feedback controller v2 refined")
    print("----------------------------------------")
    print(f"LI-850 port:             {LI850_PORT}")
    print(f"Bronkhorst port:         {BRONKHORST_PORT}")
    print(f"Bronkhorst address:      {BRONKHORST_ADDRESS}")
    print(f"MFC full scale:          {MFC_FULL_SCALE_MLN_MIN:.3f} mln/min")
    print(f"Target CO2:              {TARGET_CO2_PPM:.1f} ppm")
    print(f"Deadband:                ±{DEADBAND_PPM:.1f} ppm")
    print(f"High CO2 cutoff:         {HIGH_CO2_CUTOFF_PPM:.1f} ppm")
    print(f"Max CO2 flow:            {MAX_CO2_FLOW_MLN_MIN:.3f} mln/min")
    print(f"Control interval:        {CONTROL_INTERVAL_S:.1f} s")
    print(f"CO2 average window:      {CO2_AVERAGE_WINDOW_S:.1f} s")
    print(f"MFC avg supply window:   {MFC_SUPPLY_AVERAGE_WINDOW_S:.1f} s")
    print(f"Regression dC/dt window: {REGRESSION_DCDT_WINDOW_S:.1f} s")
    print(f"Chamber volume:          {CHAMBER_VOLUME_M3:.3f} m3")
    print(f"Chamber air temperature: {CHAMBER_AIR_TEMP_C:.2f} °C")
    print(f"Dry run:                 {DRY_RUN_NO_MFC_WRITE}")
    print("----------------------------------------\n")

    li850 = None
    mfc = None
    log_f = None

    li850_history = deque(maxlen=20000)
    mfc_history = deque(maxlen=20000)
    a_history = deque(maxlen=20000)

    total_co2_umol = 0.0

    start_time = time.time()
    last_control_time = 0.0
    last_integration_time = time.time()
    last_valid_li850_time = None

    current_setpoint = 0.0
    last_on_time = None
    last_off_time = time.time()

    integral_error = 0.0
    smoothed_feedforward_flow = 0.0
    last_config_check_time = 0.0

    try:
        print("[INIT] Opening LI-850 serial connection...")
        li850 = LI850Reader(LI850_PORT)

        print("[INIT] Connecting to Bronkhorst MFC...")
        mfc = BronkhorstMFC(
            port=BRONKHORST_PORT,
            address=BRONKHORST_ADDRESS,
            full_scale_mln_min=MFC_FULL_SCALE_MLN_MIN,
        )

        if CHECK_MFC_CONNECTION_BEFORE_CONTROL:
            ok = check_bronkhorst_mfc_connection(mfc)
            if not ok:
                print("[ABORT] MFC connection check failed.")
                mfc.close_zero()
                return

        print("[INIT] Setting MFC to zero...")
        mfc.set_flow(0.0)

        if RUN_PRECHECK_BEFORE_CONTROL:
            ok = run_precheck(li850, mfc)
            if not ok:
                print("[ABORT] Pre-check failed.")
                mfc.set_flow(0.0)
                return

        log_path = make_log_path()
        log_f, writer = open_log_file(log_path)
        print(f"[LOG] Writing to: {log_path}\n")

        print("[RUN] Controller started. Press Ctrl+C to stop.\n")

        while True:
            now = time.time()

            # ------------------------------------------------------------
            # Live settings reload
            # ------------------------------------------------------------
            if now - last_config_check_time >= CONFIG_RELOAD_INTERVAL_S:
                last_config_check_time = now
                last_config_mtime = reload_config_if_changed(config_path, last_config_mtime)

            # ------------------------------------------------------------
            # Read LI-850
            # ------------------------------------------------------------
            reading = li850.read_one()

            if reading is not None:
                li850_history.append(reading)
                last_valid_li850_time = reading.timestamp

            # ------------------------------------------------------------
            # Safety: if LI-850 data are lost, stop CO2
            # ------------------------------------------------------------
            if last_valid_li850_time is not None:
                if now - last_valid_li850_time > STOP_IF_NO_LI850_DATA_FOR_S:
                    print("[SAFETY] No LI-850 data recently. Setting MFC to zero.")
                    mfc.set_flow(0.0)
                    current_setpoint = 0.0

            # ------------------------------------------------------------
            # MFC actual flow and supply integration
            # ------------------------------------------------------------
            dt_integration_s = now - last_integration_time

            actual_flow = mfc.read_actual_flow()
            flow_for_integration = actual_flow if actual_flow is not None else current_setpoint

            co2_supply_inst_umol_s = normal_flow_to_co2_umol_s(
                flow_for_integration,
                CO2_FRACTION_IN_MFC_GAS,
            )

            co2_umol_step = 0.0
            if dt_integration_s > 0:
                co2_umol_step = co2_supply_inst_umol_s * dt_integration_s
                total_co2_umol += co2_umol_step
                last_integration_time = now

            mfc_history.append(
                MFCRecord(
                    timestamp=now,
                    setpoint_mln_min=current_setpoint,
                    actual_mln_min=actual_flow,
                    flow_used_mln_min=flow_for_integration,
                    co2_supply_umol_s=co2_supply_inst_umol_s,
                )
            )

            # ------------------------------------------------------------
            # Control update
            # ------------------------------------------------------------
            if now - last_control_time >= CONTROL_INTERVAL_S:
                dt_control_s = now - last_control_time if last_control_time > 0 else CONTROL_INTERVAL_S
                last_control_time = now

                co2_avg = moving_average_co2(li850_history, CO2_AVERAGE_WINDOW_S)
                latest = latest_reading(li850_history)

                if co2_avg is None or latest is None:
                    print("[WAIT] Not enough LI-850 data yet. MFC remains zero.")
                    mfc.set_flow(0.0)
                    current_setpoint = 0.0
                    time.sleep(0.1)
                    continue

                co2_error = TARGET_CO2_PPM - co2_avg

                chamber_n_air = chamber_air_moles(
                    volume_m3=CHAMBER_VOLUME_M3,
                    pressure_kpa=latest.cellpres_kpa,
                    chamber_air_temp_c=CHAMBER_AIR_TEMP_C,
                )

                # --------------------------------------------------------
                # dC/dt calculations
                # --------------------------------------------------------
                dcdt_simple_ppm_s = calculate_dcdt_simple_ppm_s(
                    li850_history,
                    DCDT_WINDOW_S,
                )

                if USE_REGRESSION_DCDT:
                    dcdt_regression_ppm_s = calculate_dcdt_regression_ppm_s(
                        li850_history,
                        REGRESSION_DCDT_WINDOW_S,
                    )
                    dcdt_regression_short_ppm_s = calculate_dcdt_regression_ppm_s(
                        li850_history,
                        REGRESSION_DCDT_SHORT_WINDOW_S,
                    )
                else:
                    dcdt_regression_ppm_s = None
                    dcdt_regression_short_ppm_s = None

                # --------------------------------------------------------
                # Average MFC CO2 supply, lag-corrected
                # --------------------------------------------------------
                co2_supply_avg_umol_s = moving_average_mfc_supply(
                    mfc_history,
                    MFC_SUPPLY_AVERAGE_WINDOW_S,
                    lag_s=MFC_TO_LI850_LAG_S,
                )

                if co2_supply_avg_umol_s is None:
                    co2_supply_avg_umol_s = co2_supply_inst_umol_s

                # Direct MFC-only assimilation estimate.
                # Most valid when chamber CO2 is stable and storage correction is negligible.
                A_MFC_only_umol_s = co2_supply_avg_umol_s

                # --------------------------------------------------------
                # Storage terms
                # n_air mol * ppm/s = umol/s
                # --------------------------------------------------------
                storage_simple_umol_s = (
                    chamber_n_air * dcdt_simple_ppm_s
                    if dcdt_simple_ppm_s is not None
                    else None
                )

                storage_regression_umol_s = (
                    chamber_n_air * dcdt_regression_ppm_s
                    if dcdt_regression_ppm_s is not None
                    else None
                )

                storage_regression_short_umol_s = (
                    chamber_n_air * dcdt_regression_short_ppm_s
                    if dcdt_regression_short_ppm_s is not None
                    else None
                )

                # --------------------------------------------------------
                # Assimilation estimates
                # A = CO2 supply - chamber storage
                # --------------------------------------------------------
                A_inst_umol_s = (
                    co2_supply_inst_umol_s - storage_simple_umol_s
                    if storage_simple_umol_s is not None
                    else None
                )

                A_from_avg_supply_umol_s = (
                    co2_supply_avg_umol_s - storage_simple_umol_s
                    if storage_simple_umol_s is not None
                    else None
                )

                A_regression_umol_s = (
                    co2_supply_avg_umol_s - storage_regression_umol_s
                    if storage_regression_umol_s is not None
                    else None
                )

                A_regression_short_umol_s = (
                    co2_supply_avg_umol_s - storage_regression_short_umol_s
                    if storage_regression_short_umol_s is not None
                    else None
                )

                a_history.append(
                    ARecord(
                        timestamp=now,
                        A_inst_umol_s=A_inst_umol_s,
                        A_MFC_only_umol_s=A_MFC_only_umol_s,
                        A_from_avg_supply_umol_s=A_from_avg_supply_umol_s,
                        A_regression_umol_s=A_regression_umol_s,
                        A_regression_short_umol_s=A_regression_short_umol_s,
                    )
                )

                A_smoothed_umol_s = moving_average_A(
                    a_history,
                    "A_from_avg_supply_umol_s",
                    A_SMOOTHING_WINDOW_S,
                )

                A_regression_smoothed_umol_s = moving_average_A(
                    a_history,
                    "A_regression_umol_s",
                    A_REGRESSION_SMOOTHING_WINDOW_S,
                )

                A_MFC_only_smoothed_umol_s = moving_average_A(
                    a_history,
                    "A_MFC_only_umol_s",
                    A_SMOOTHING_WINDOW_S,
                )

                # Net exchange based on the regression-corrected mass balance.
                # Sign convention:
                #   A_regression > 0  = net CO2 uptake
                #   NE_regression > 0 = net CO2 release
                NE_regression_umol_s = (
                    storage_regression_umol_s - co2_supply_avg_umol_s
                    if storage_regression_umol_s is not None
                    else None
                )

                # --------------------------------------------------------
                # Feedforward estimate
                # Prefer smoothed regression A if available.
                # Fallback to average-supply A.
                # --------------------------------------------------------
                recent_A_for_feedforward = moving_average_A(
                    a_history,
                    "A_regression_umol_s",
                    FEEDFORWARD_A_WINDOW_S,
                )

                if recent_A_for_feedforward is None:
                    recent_A_for_feedforward = moving_average_A(
                        a_history,
                        "A_from_avg_supply_umol_s",
                        FEEDFORWARD_A_WINDOW_S,
                    )

                if recent_A_for_feedforward is not None and recent_A_for_feedforward > 0:
                    raw_feedforward_flow = co2_umol_s_to_normal_flow_mln_min(
                        recent_A_for_feedforward,
                        CO2_FRACTION_IN_MFC_GAS,
                    )
                    raw_feedforward_flow = max(
                        0.0,
                        min(raw_feedforward_flow, MAX_FEEDFORWARD_FLOW_MLN_MIN),
                    )
                else:
                    raw_feedforward_flow = 0.0

                smoothed_feedforward_flow = (
                    FEEDFORWARD_SMOOTHING_ALPHA * raw_feedforward_flow
                    + (1.0 - FEEDFORWARD_SMOOTHING_ALPHA) * smoothed_feedforward_flow
                )

                # --------------------------------------------------------
                # Raw feedback command
                # --------------------------------------------------------
                raw_requested_setpoint, integral_error, control_note = calculate_feedback_setpoint(
                    co2_avg_ppm=co2_avg,
                    previous_cmd=current_setpoint,
                    integral_error=integral_error,
                    dt_s=dt_control_s,
                    feedforward_flow_mln_min=smoothed_feedforward_flow,
                    dcdt_regression_ppm_s=dcdt_regression_ppm_s,
                )

                # Final maximum clamp
                raw_requested_setpoint = max(
                    0.0,
                    min(raw_requested_setpoint, MAX_CO2_FLOW_MLN_MIN),
                )

                # --------------------------------------------------------
                # Manual MFC override
                # --------------------------------------------------------
                # If enabled, command the user-defined MFC flow directly.
                # This bypasses feedback/feedforward, rate limiting and min ON/OFF timing.
                # The high-CO2 safety cutoff below still forces the MFC to zero.
                if MANUAL_MFC_OVERRIDE_ENABLED:
                    manual_setpoint = max(
                        0.0,
                        min(
                            MANUAL_MFC_FLOW_MLN_MIN,
                            MFC_FULL_SCALE_MLN_MIN,
                            MAX_CO2_FLOW_MLN_MIN,
                        ),
                    )
                    raw_requested_setpoint = manual_setpoint
                    rate_limited_setpoint = manual_setpoint
                    final_setpoint = manual_setpoint
                    integral_error = 0.0
                    control_note = "manual MFC override"

                    was_on = current_setpoint > MIN_EFFECTIVE_MFC_FLOW_MLN_MIN
                    is_on = final_setpoint > MIN_EFFECTIVE_MFC_FLOW_MLN_MIN
                    if not was_on and is_on:
                        last_on_time = now
                    if was_on and not is_on:
                        last_off_time = now
                else:
                    # Rate limiting
                    rate_limited_setpoint = apply_rate_limit(
                        raw_requested_setpoint,
                        current_setpoint,
                    )

                    # Minimum ON/OFF time
                    final_setpoint, last_on_time, last_off_time = apply_min_on_off_time(
                        requested_cmd=rate_limited_setpoint,
                        previous_cmd=current_setpoint,
                        now=now,
                        last_on_time=last_on_time,
                        last_off_time=last_off_time,
                    )

                # Final safety clamp.
                # This bypasses manual override and minimum ON/OFF time for safety.
                if co2_avg >= HIGH_CO2_CUTOFF_PPM:
                    if current_setpoint > MIN_EFFECTIVE_MFC_FLOW_MLN_MIN:
                        last_off_time = now
                    final_setpoint = 0.0
                    integral_error = 0.0
                    control_note = "final safety clamp; high CO2 cutoff"

                # Small values to zero
                if final_setpoint < MIN_EFFECTIVE_MFC_FLOW_MLN_MIN:
                    final_setpoint = 0.0

                # Physical clamp
                final_setpoint = max(
                    0.0,
                    min(final_setpoint, MFC_FULL_SCALE_MLN_MIN, MAX_CO2_FLOW_MLN_MIN),
                )

                ok = mfc.set_flow(final_setpoint)
                if ok:
                    current_setpoint = final_setpoint
                else:
                    current_setpoint = 0.0
                    control_note = "MFC write failed; setpoint set to zero"

                # Read actual flow after setpoint change
                actual_flow_after = mfc.read_actual_flow()
                actual_flow_for_log = actual_flow_after if actual_flow_after is not None else actual_flow

                if actual_flow_for_log is None:
                    actual_flow_for_log = current_setpoint

                elapsed_s = now - start_time

                # --------------------------------------------------------
                # Area-based values
                # --------------------------------------------------------
                if LEAF_AREA_M2 is not None and LEAF_AREA_M2 > 0:
                    A_inst_area = (
                        A_inst_umol_s / LEAF_AREA_M2
                        if A_inst_umol_s is not None
                        else None
                    )
                    A_avg_area = (
                        A_from_avg_supply_umol_s / LEAF_AREA_M2
                        if A_from_avg_supply_umol_s is not None
                        else None
                    )
                    A_MFC_only_area = A_MFC_only_umol_s / LEAF_AREA_M2
                    A_reg_area = (
                        A_regression_umol_s / LEAF_AREA_M2
                        if A_regression_umol_s is not None
                        else None
                    )
                    A_smooth_area = (
                        A_smoothed_umol_s / LEAF_AREA_M2
                        if A_smoothed_umol_s is not None
                        else None
                    )
                else:
                    A_inst_area = None
                    A_avg_area = None
                    A_MFC_only_area = None
                    A_reg_area = None
                    A_smooth_area = None

                # --------------------------------------------------------
                # CSV logging
                # --------------------------------------------------------
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    f"{elapsed_s:.1f}",

                    f"{latest.co2_ppm:.3f}",
                    f"{co2_avg:.3f}",
                    f"{TARGET_CO2_PPM:.3f}",
                    f"{co2_error:.3f}",

                    "" if latest.h2o_mmol_mol is None else f"{latest.h2o_mmol_mol:.6f}",
                    "" if latest.cellpres_kpa is None else f"{latest.cellpres_kpa:.6f}",
                    "" if latest.celltemp_c is None else f"{latest.celltemp_c:.6f}",
                    "" if latest.flowrate is None else f"{latest.flowrate:.6f}",

                    f"{CHAMBER_AIR_TEMP_C:.6f}",
                    f"{chamber_n_air:.6f}",

                    f"{raw_requested_setpoint:.6f}",
                    f"{rate_limited_setpoint:.6f}",
                    f"{current_setpoint:.6f}",
                    "" if actual_flow_after is None else f"{actual_flow_after:.6f}",
                    f"{actual_flow_for_log:.6f}",
                    f"{int(MANUAL_MFC_OVERRIDE_ENABLED)}",
                    f"{MANUAL_MFC_FLOW_MLN_MIN:.6f}",

                    f"{co2_supply_inst_umol_s:.6f}",
                    f"{co2_supply_avg_umol_s:.6f}",
                    f"{A_MFC_only_umol_s:.6f}",
                    "" if A_MFC_only_smoothed_umol_s is None else f"{A_MFC_only_smoothed_umol_s:.6f}",
                    f"{co2_umol_step:.6f}",
                    f"{total_co2_umol / 1000.0:.6f}",

                    f"{smoothed_feedforward_flow:.6f}",
                    f"{raw_feedforward_flow:.6f}",

                    "" if dcdt_simple_ppm_s is None else f"{dcdt_simple_ppm_s:.8f}",
                    "" if dcdt_regression_ppm_s is None else f"{dcdt_regression_ppm_s:.8f}",
                    "" if dcdt_regression_short_ppm_s is None else f"{dcdt_regression_short_ppm_s:.8f}",

                    "" if storage_simple_umol_s is None else f"{storage_simple_umol_s:.6f}",
                    "" if storage_regression_umol_s is None else f"{storage_regression_umol_s:.6f}",
                    "" if storage_regression_short_umol_s is None else f"{storage_regression_short_umol_s:.6f}",

                    "" if A_inst_umol_s is None else f"{A_inst_umol_s:.6f}",
                    "" if A_from_avg_supply_umol_s is None else f"{A_from_avg_supply_umol_s:.6f}",
                    "" if A_regression_umol_s is None else f"{A_regression_umol_s:.6f}",
                    "" if A_regression_short_umol_s is None else f"{A_regression_short_umol_s:.6f}",

                    "" if A_smoothed_umol_s is None else f"{A_smoothed_umol_s:.6f}",
                    "" if A_regression_smoothed_umol_s is None else f"{A_regression_smoothed_umol_s:.6f}",
                    "" if NE_regression_umol_s is None else f"{NE_regression_umol_s:.6f}",

                    "" if A_inst_area is None else f"{A_inst_area:.6f}",
                    "" if A_avg_area is None else f"{A_avg_area:.6f}",
                    "" if A_MFC_only_area is None else f"{A_MFC_only_area:.6f}",
                    "" if A_reg_area is None else f"{A_reg_area:.6f}",
                    "" if A_smooth_area is None else f"{A_smooth_area:.6f}",

                    control_note,
                ])
                log_f.flush()

                # --------------------------------------------------------
                # Terminal display
                # --------------------------------------------------------
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} | "
                    f"CO2={latest.co2_ppm:.1f} ppm | "
                    f"avg={co2_avg:.1f} ppm | "
                    f"err={co2_error:+.1f} ppm | "
                    f"dC/dt simple={safe_fmt(dcdt_simple_ppm_s, 5)} ppm/s | "
                    f"dC/dt reg={safe_fmt(dcdt_regression_ppm_s, 5)} ppm/s | "
                    f"MFC set={current_setpoint:.2f} mln/min | "
                    f"MFC act={safe_fmt(actual_flow_after, 2)} mln/min | "
                    f"supply avg={co2_supply_avg_umol_s:.2f} umol/s | "
                    f"A MFC={A_MFC_only_umol_s:.2f} umol/s | "
                    f"A MFC smooth={safe_fmt(A_MFC_only_smoothed_umol_s, 2)} umol/s | "
                    f"A avg={safe_fmt(A_from_avg_supply_umol_s, 2)} umol/s | "
                    f"A reg={safe_fmt(A_regression_umol_s, 2)} umol/s | "
                    f"A smooth={safe_fmt(A_regression_smoothed_umol_s, 2)} umol/s | "
                    f"NE reg={safe_fmt(NE_regression_umol_s, 2)} umol/s | "
                    f"FF={smoothed_feedforward_flow:.2f} mln/min | "
                    f"Manual={int(MANUAL_MFC_OVERRIDE_ENABLED)} "
                    f"({MANUAL_MFC_FLOW_MLN_MIN:.2f} mln/min) | "
                    f"{control_note}"
                )

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C received.")

    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        raise

    finally:
        if mfc is not None and SET_MFC_ZERO_ON_EXIT:
            print("[EXIT] Setting MFC to zero...")
            mfc.close_zero()

        if li850 is not None:
            li850.close()

        if log_f is not None:
            log_f.close()

        print("[EXIT] Done.")


if __name__ == "__main__":
    main()