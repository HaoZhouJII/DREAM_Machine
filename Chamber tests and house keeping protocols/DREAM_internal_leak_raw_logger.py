#!/usr/bin/env python3
"""
DREAM_internal_leak_raw_logger.py

Standalone raw-data logger for the DREAM empty-chamber internal leak test.

Purpose
-------
This script DOES NOT fit or analyse the leak rate during the experiment.
It only controls the multiplexer and CO2 MFC, logs raw LI-850 readings,
logs valve-switch and MFC-command event times, and records timing columns
needed to analyse injection lag, measurement lag, multiplexer carryover,
room CO2 correction, and chamber CO2 decay afterwards.

Default sequence
----------------
1) 10 min chamber baseline:
   - LI-850 samples valve 1&2.
   - Valve 3 is bypassed by the multiplexer.

2) 10 min room baseline:
   - LI-850 samples valve 3.
   - Valves 1&2 are bypassed.

3) 2 min pre-injection stabilisation:
   - LI-850 samples valve 1&2.

4) CO2 injection:
   - CO2 MFC is set to 200 mLn/min.
   - LI-850 continuously samples valve 1&2.
   - Injection stops when LI-850 CO2 >= 1000 ppm, or at safety cutoff/timeout.

5) 6 h post-injection raw decay logging:
   - 10 min valve 1&2 chamber sampling.
   - 2 min valve 3 room sampling.
   - Repeat until duration is complete.

Outputs
-------
A timestamped folder containing:
    1. *_raw_li850.csv      every valid LI-850 reading
    2. *_events.csv         valve switches, MFC on/off, start/stop markers
    3. *_run_metadata.json  settings used for the run

Dependencies
------------
    python -m pip install pyserial bronkhorst-propar

Important
---------
Close the CO2 feedback controller and multiplexer GUI before running this script,
because they will otherwise occupy the same COM ports.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import serial

try:
    import propar
except ImportError:
    propar = None


# ============================================================
# Default hardware settings
# ============================================================
DEFAULT_LI850_PORT = "COM11"
DEFAULT_MUX_PORT = "COM31"
DEFAULT_MFC_PORT = "COM5"       # Change with --mfc-port if needed.
DEFAULT_MFC_ADDRESS = 6

LI850_BAUDRATE = 9600
LI850_TIMEOUT_S = 1.0
MUX_BAUDRATE = 115200
MUX_TIMEOUT_S = 0.2
BRONKHORST_BAUDRATE = 38400
MFC_FULL_SCALE_MLN_MIN = 200.0

# Default protocol timing
BASELINE_CHAMBER_S = 10 * 60
BASELINE_ROOM_S = 10 * 60
PRE_INJECTION_STABILIZE_S = 2 * 60
DECAY_TOTAL_S = 6 * 60 * 60
DECAY_CHAMBER_SAMPLE_S = 10 * 60
DECAY_ROOM_SAMPLE_S = 2 * 60

# Injection settings
INJECTION_FLOW_MLN_MIN = 200.0
INJECTION_TARGET_CO2_PPM = 1000.0
INJECTION_SAFETY_CUTOFF_PPM = 1200.0
INJECTION_MAX_DURATION_S = 20 * 60

# Logging
LOG_FOLDER = "DREAM_internal_leak_raw_logs"
LOG_PREFIX = "DREAM_internal_leak_raw"


@dataclass
class LI850Reading:
    timestamp: float
    co2_ppm: float
    h2o_mmol_mol: Optional[float] = None
    celltemp_c: Optional[float] = None
    cellpres_kpa: Optional[float] = None
    flowrate: Optional[float] = None
    raw_xml: str = ""


class LI850Reader:
    def __init__(self, port: str, baudrate: int = LI850_BAUDRATE, timeout_s: float = LI850_TIMEOUT_S):
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout_s,
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
    def _get_float(parent, tag: str) -> Optional[float]:
        elem = parent.find(tag)
        if elem is None or elem.text is None:
            return None
        try:
            return float(elem.text.strip())
        except ValueError:
            return None

    def read_one(self) -> Optional[LI850Reading]:
        try:
            line_bytes = self.ser.readline()
        except Exception as exc:
            print(f"[LI850] Serial read error: {exc}")
            return None

        if not line_bytes:
            return None

        text = line_bytes.decode(errors="replace").strip()
        if not text or "<li850>" not in text or "</li850>" not in text:
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


class MultiplexerClient:
    def __init__(self, port: str, baudrate: int = MUX_BAUDRATE):
        self.ser = serial.Serial(port=port, baudrate=baudrate, timeout=MUX_TIMEOUT_S)
        time.sleep(1.0)
        self.send({"cmd": "identify"})
        self.send({"cmd": "status"})
        self._drain_status(1.0)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def send(self, obj: dict):
        line = json.dumps(obj) + "\n"
        self.ser.write(line.encode("utf-8"))
        self.ser.flush()

    def set_sample(self, sample: str) -> str:
        self.send({"cmd": "sample", "sample": sample})
        return self._drain_status(1.0)

    def status(self) -> str:
        self.send({"cmd": "status"})
        return self._drain_status(1.0)

    def _drain_status(self, duration_s: float = 0.5) -> str:
        end = time.time() + duration_s
        last_line = ""
        while time.time() < end:
            try:
                raw = self.ser.readline()
            except Exception:
                break
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if line:
                last_line = line
                print(f"[MUX] {line}")
        return last_line


class BronkhorstMFC:
    def __init__(self, port: str, address: int, full_scale_mln_min: float):
        if propar is None:
            raise RuntimeError(
                "bronkhorst-propar is not installed. Install with: python -m pip install bronkhorst-propar"
            )
        self.port = port
        self.address = address
        self.full_scale = float(full_scale_mln_min)
        self.instrument = propar.instrument(port, address=address, baudrate=BRONKHORST_BAUDRATE)

    def mln_min_to_raw(self, flow_mln_min: float) -> int:
        flow = max(0.0, min(float(flow_mln_min), self.full_scale))
        raw = round(32000.0 * flow / self.full_scale)
        return int(max(0, min(raw, 32000)))

    def raw_to_mln_min(self, raw_value) -> Optional[float]:
        if raw_value is None:
            return None
        try:
            return float(raw_value) * self.full_scale / 32000.0
        except Exception:
            return None

    def set_flow(self, flow_mln_min: float) -> bool:
        raw = self.mln_min_to_raw(flow_mln_min)
        try:
            self.instrument.setpoint = raw
            return True
        except Exception as exc:
            print(f"[MFC] Failed to set flow: {exc}")
            return False

    def read_actual_flow(self) -> Optional[float]:
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


class RawLogger:
    def __init__(self, out_dir: Path, prefix: str, include_raw_xml: bool = False):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.include_raw_xml = include_raw_xml
        self.raw_path = out_dir / f"{prefix}_raw_li850.csv"
        self.events_path = out_dir / f"{prefix}_events.csv"
        self.raw_f = open(self.raw_path, "w", newline="", encoding="utf-8")
        self.events_f = open(self.events_path, "w", newline="", encoding="utf-8")
        self.raw_writer = csv.writer(self.raw_f)
        self.events_writer = csv.writer(self.events_f)

        raw_header = [
            "timestamp",
            "epoch_s",
            "elapsed_s",
            "phase",
            "sample_to_li850",
            "sample_switch_index",
            "seconds_since_sample_switch",
            "mfc_command_index",
            "mfc_setpoint_mln_min",
            "mfc_actual_mln_min",
            "seconds_since_mfc_command",
            "seconds_since_injection_start",
            "seconds_since_injection_stop",
            "li850_co2_ppm",
            "li850_h2o_mmol_mol",
            "li850_cellpres_kpa",
            "li850_celltemp_c",
            "li850_flowrate",
            "event_marker",
            "note",
        ]
        if include_raw_xml:
            raw_header.append("li850_raw_xml")
        self.raw_writer.writerow(raw_header)

        self.events_writer.writerow([
            "timestamp",
            "epoch_s",
            "elapsed_s",
            "event",
            "phase",
            "sample_to_li850",
            "mfc_setpoint_mln_min",
            "mfc_actual_mln_min",
            "details",
        ])
        self.raw_f.flush()
        self.events_f.flush()

    def close(self):
        try:
            self.raw_f.flush()
            self.raw_f.close()
        except Exception:
            pass
        try:
            self.events_f.flush()
            self.events_f.close()
        except Exception:
            pass

    @staticmethod
    def ts(epoch: float) -> str:
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @staticmethod
    def fmt(value, digits: int = 6) -> str:
        if value is None:
            return ""
        try:
            return f"{float(value):.{digits}f}"
        except Exception:
            return str(value)

    def log_event(
        self,
        *,
        start_time: float,
        event: str,
        phase: str,
        sample: str,
        mfc_setpoint: float,
        mfc_actual: Optional[float],
        details: str = "",
    ):
        now = time.time()
        self.events_writer.writerow([
            self.ts(now),
            f"{now:.6f}",
            f"{now - start_time:.3f}",
            event,
            phase,
            sample,
            f"{mfc_setpoint:.6f}",
            self.fmt(mfc_actual, 6),
            details,
        ])
        self.events_f.flush()
        print(f"[EVENT] {event} | phase={phase} | sample={sample} | MFC={mfc_setpoint:.3f} | {details}")

    def log_reading(
        self,
        *,
        start_time: float,
        reading: LI850Reading,
        phase: str,
        sample: str,
        sample_switch_index: int,
        sample_switch_time: Optional[float],
        mfc_command_index: int,
        mfc_setpoint: float,
        mfc_actual: Optional[float],
        mfc_command_time: Optional[float],
        injection_start_time: Optional[float],
        injection_stop_time: Optional[float],
        event_marker: str = "",
        note: str = "",
    ):
        now = reading.timestamp
        row = [
            self.ts(now),
            f"{now:.6f}",
            f"{now - start_time:.3f}",
            phase,
            sample,
            sample_switch_index,
            "" if sample_switch_time is None else f"{now - sample_switch_time:.3f}",
            mfc_command_index,
            f"{mfc_setpoint:.6f}",
            self.fmt(mfc_actual, 6),
            "" if mfc_command_time is None else f"{now - mfc_command_time:.3f}",
            "" if injection_start_time is None else f"{now - injection_start_time:.3f}",
            "" if injection_stop_time is None else f"{now - injection_stop_time:.3f}",
            f"{reading.co2_ppm:.3f}",
            self.fmt(reading.h2o_mmol_mol, 6),
            self.fmt(reading.cellpres_kpa, 6),
            self.fmt(reading.celltemp_c, 6),
            self.fmt(reading.flowrate, 6),
            event_marker,
            note,
        ]
        if self.include_raw_xml:
            row.append(reading.raw_xml)
        self.raw_writer.writerow(row)
        self.raw_f.flush()


def safe_fmt(value, digits=2) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "NA"


def make_run_folder(base_folder: str, prefix: str) -> tuple[Path, str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_prefix = f"{prefix}_{stamp}"
    out_dir = Path(base_folder) / run_prefix
    return out_dir, run_prefix


def write_metadata(out_dir: Path, run_prefix: str, args: argparse.Namespace):
    path = out_dir / f"{run_prefix}_run_metadata.json"
    metadata = {
        "created_local_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "script": "DREAM_internal_leak_raw_logger.py",
        "purpose": "Raw acquisition only; no leak fitting or lag analysis during run.",
        "args": vars(args),
        "protocol": {
            "baseline_chamber_s": args.baseline_chamber_s,
            "baseline_room_s": args.baseline_room_s,
            "pre_injection_stabilize_s": args.pre_injection_stabilize_s,
            "decay_total_s": args.decay_hours * 3600.0,
            "decay_chamber_sample_s": args.decay_chamber_sample_s,
            "decay_room_sample_s": args.decay_room_sample_s,
            "chamber_sample": "1,2",
            "room_sample": "3",
        },
        "notes_for_lag_analysis": [
            "Use events.csv mfc_on_command as t0 for injection-response lag.",
            "Use raw_li850.csv seconds_since_injection_start to identify first detectable CO2 rise.",
            "Use events.csv mfc_off_command / injection_stop_target_reached to examine post-injection delay and overshoot.",
            "Use seconds_since_sample_switch to quantify multiplexer/tubing carryover after switching 1,2 <-> 3.",
        ],
    }
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return path


class RunState:
    def __init__(self):
        self.phase = "initialising"
        self.sample = ""
        self.sample_switch_index = 0
        self.sample_switch_time: Optional[float] = None
        self.mfc_command_index = 0
        self.mfc_setpoint = 0.0
        self.mfc_command_time: Optional[float] = None
        self.injection_start_time: Optional[float] = None
        self.injection_stop_time: Optional[float] = None
        self.last_mfc_actual: Optional[float] = None
        self.last_mfc_read_time = 0.0


def read_mfc_actual_periodically(mfc: BronkhorstMFC, state: RunState, interval_s: float = 2.0) -> Optional[float]:
    now = time.time()
    if now - state.last_mfc_read_time >= interval_s:
        state.last_mfc_actual = mfc.read_actual_flow()
        state.last_mfc_read_time = now
    return state.last_mfc_actual


def set_mux_sample(
    mux: MultiplexerClient,
    logger: RawLogger,
    start_time: float,
    state: RunState,
    sample: str,
    details: str = "",
):
    state.sample_switch_index += 1
    state.sample = sample
    state.sample_switch_time = time.time()
    status = mux.set_sample(sample)
    logger.log_event(
        start_time=start_time,
        event="sample_switch_command",
        phase=state.phase,
        sample=sample,
        mfc_setpoint=state.mfc_setpoint,
        mfc_actual=state.last_mfc_actual,
        details=f"{details}; mux_status={status}",
    )


def set_mfc_flow(
    mfc: BronkhorstMFC,
    logger: RawLogger,
    start_time: float,
    state: RunState,
    flow_mln_min: float,
    event: str,
    details: str = "",
):
    ok = mfc.set_flow(flow_mln_min)
    state.mfc_command_index += 1
    state.mfc_setpoint = float(flow_mln_min) if ok else 0.0
    state.mfc_command_time = time.time()
    state.last_mfc_actual = mfc.read_actual_flow()
    state.last_mfc_read_time = time.time()
    logger.log_event(
        start_time=start_time,
        event=event,
        phase=state.phase,
        sample=state.sample,
        mfc_setpoint=state.mfc_setpoint,
        mfc_actual=state.last_mfc_actual,
        details=("OK" if ok else "FAILED") + (f"; {details}" if details else ""),
    )
    if not ok:
        raise RuntimeError(f"Failed to set MFC flow to {flow_mln_min}")


def acquire_for_duration(
    *,
    li850: LI850Reader,
    mfc: BronkhorstMFC,
    logger: RawLogger,
    start_time: float,
    state: RunState,
    duration_s: float,
    note: str = "",
    status_interval_s: float = 30.0,
):
    end = time.time() + duration_s
    last_status = 0.0
    while time.time() < end:
        reading = li850.read_one()
        if reading is None:
            continue
        actual = read_mfc_actual_periodically(mfc, state)
        logger.log_reading(
            start_time=start_time,
            reading=reading,
            phase=state.phase,
            sample=state.sample,
            sample_switch_index=state.sample_switch_index,
            sample_switch_time=state.sample_switch_time,
            mfc_command_index=state.mfc_command_index,
            mfc_setpoint=state.mfc_setpoint,
            mfc_actual=actual,
            mfc_command_time=state.mfc_command_time,
            injection_start_time=state.injection_start_time,
            injection_stop_time=state.injection_stop_time,
            note=note,
        )
        now = time.time()
        if now - last_status >= status_interval_s:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] {state.phase} | sample={state.sample} | "
                f"CO2={reading.co2_ppm:.1f} ppm | MFC set={state.mfc_setpoint:.1f} | "
                f"MFC act={safe_fmt(actual, 2)} | remaining={max(0.0, end-now)/60:.1f} min"
            )
            last_status = now


def acquire_injection_until_target(
    *,
    li850: LI850Reader,
    mfc: BronkhorstMFC,
    logger: RawLogger,
    start_time: float,
    state: RunState,
    target_co2_ppm: float,
    safety_cutoff_ppm: float,
    max_duration_s: float,
):
    end = time.time() + max_duration_s
    last_status = 0.0
    while time.time() < end:
        reading = li850.read_one()
        if reading is None:
            continue
        actual = read_mfc_actual_periodically(mfc, state)
        event_marker = ""
        if reading.co2_ppm >= target_co2_ppm:
            event_marker = "target_reached_on_this_reading"
        elif reading.co2_ppm >= safety_cutoff_ppm:
            event_marker = "safety_cutoff_on_this_reading"

        logger.log_reading(
            start_time=start_time,
            reading=reading,
            phase=state.phase,
            sample=state.sample,
            sample_switch_index=state.sample_switch_index,
            sample_switch_time=state.sample_switch_time,
            mfc_command_index=state.mfc_command_index,
            mfc_setpoint=state.mfc_setpoint,
            mfc_actual=actual,
            mfc_command_time=state.mfc_command_time,
            injection_start_time=state.injection_start_time,
            injection_stop_time=state.injection_stop_time,
            event_marker=event_marker,
            note="CO2 MFC active for chamber enrichment",
        )

        now = time.time()
        if now - last_status >= 5.0:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] injection | sample={state.sample} | "
                f"CO2={reading.co2_ppm:.1f} ppm | MFC act={safe_fmt(actual, 2)}"
            )
            last_status = now

        if reading.co2_ppm >= safety_cutoff_ppm:
            logger.log_event(
                start_time=start_time,
                event="injection_safety_cutoff_reached",
                phase=state.phase,
                sample=state.sample,
                mfc_setpoint=state.mfc_setpoint,
                mfc_actual=actual,
                details=f"LI850 CO2={reading.co2_ppm:.3f} ppm >= safety cutoff {safety_cutoff_ppm:.3f} ppm",
            )
            return "safety_cutoff", reading.co2_ppm

        if reading.co2_ppm >= target_co2_ppm:
            logger.log_event(
                start_time=start_time,
                event="injection_target_reached",
                phase=state.phase,
                sample=state.sample,
                mfc_setpoint=state.mfc_setpoint,
                mfc_actual=actual,
                details=f"LI850 CO2={reading.co2_ppm:.3f} ppm >= target {target_co2_ppm:.3f} ppm",
            )
            return "target_reached", reading.co2_ppm

    logger.log_event(
        start_time=start_time,
        event="injection_timeout",
        phase=state.phase,
        sample=state.sample,
        mfc_setpoint=state.mfc_setpoint,
        mfc_actual=state.last_mfc_actual,
        details=f"Target {target_co2_ppm:.3f} ppm not reached within {max_duration_s:.1f} s",
    )
    return "timeout", None


def parse_args():
    p = argparse.ArgumentParser(description="DREAM raw internal leak logger with multiplexer and CO2 MFC timing markers.")
    p.add_argument("--li850-port", default=DEFAULT_LI850_PORT)
    p.add_argument("--mux-port", default=DEFAULT_MUX_PORT)
    p.add_argument("--mfc-port", default=DEFAULT_MFC_PORT)
    p.add_argument("--mfc-address", type=int, default=DEFAULT_MFC_ADDRESS)
    p.add_argument("--mfc-full-scale", type=float, default=MFC_FULL_SCALE_MLN_MIN)
    p.add_argument("--injection-flow", type=float, default=INJECTION_FLOW_MLN_MIN)
    p.add_argument("--target-co2", type=float, default=INJECTION_TARGET_CO2_PPM)
    p.add_argument("--safety-cutoff", type=float, default=INJECTION_SAFETY_CUTOFF_PPM)
    p.add_argument("--injection-max-duration-s", type=float, default=INJECTION_MAX_DURATION_S)
    p.add_argument("--decay-hours", type=float, default=6.0)
    p.add_argument("--baseline-chamber-s", type=float, default=BASELINE_CHAMBER_S)
    p.add_argument("--baseline-room-s", type=float, default=BASELINE_ROOM_S)
    p.add_argument("--pre-injection-stabilize-s", type=float, default=PRE_INJECTION_STABILIZE_S)
    p.add_argument("--decay-chamber-sample-s", type=float, default=DECAY_CHAMBER_SAMPLE_S)
    p.add_argument("--decay-room-sample-s", type=float, default=DECAY_ROOM_SAMPLE_S)
    p.add_argument("--log-folder", default=LOG_FOLDER)
    p.add_argument("--log-prefix", default=LOG_PREFIX)
    p.add_argument("--include-raw-xml", action="store_true", help="Also write the complete LI-850 XML line into the CSV.")
    return p.parse_args()


def main():
    args = parse_args()
    out_dir, run_prefix = make_run_folder(args.log_folder, args.log_prefix)
    logger = RawLogger(out_dir, run_prefix, include_raw_xml=args.include_raw_xml)
    metadata_path = write_metadata(out_dir, run_prefix, args)

    print("\nDREAM internal leak RAW logger")
    print("--------------------------------")
    print(f"LI-850 port:        {args.li850_port}")
    print(f"Multiplexer port:   {args.mux_port}")
    print(f"CO2 MFC port:       {args.mfc_port}")
    print(f"CO2 MFC address:    {args.mfc_address}")
    print(f"Injection flow:     {args.injection_flow:.3f} mLn/min")
    print(f"Target CO2:         {args.target_co2:.1f} ppm")
    print(f"Decay duration:     {args.decay_hours:.2f} h")
    print(f"Output folder:      {out_dir}")
    print(f"Raw CSV:            {logger.raw_path}")
    print(f"Events CSV:         {logger.events_path}")
    print(f"Metadata JSON:      {metadata_path}")
    print("--------------------------------\n")

    li850 = None
    mux = None
    mfc = None
    state = RunState()
    start_time = time.time()

    try:
        li850 = LI850Reader(args.li850_port)
        mux = MultiplexerClient(args.mux_port)
        mfc = BronkhorstMFC(args.mfc_port, args.mfc_address, args.mfc_full_scale)

        logger.log_event(
            start_time=start_time,
            event="run_start",
            phase=state.phase,
            sample=state.sample,
            mfc_setpoint=state.mfc_setpoint,
            mfc_actual=None,
            details="Raw data acquisition started",
        )

        state.phase = "mfc_zero_initial"
        set_mfc_flow(mfc, logger, start_time, state, 0.0, "mfc_zero_initial", "Initial safety zero")
        time.sleep(1.0)

        # 1. Chamber baseline, valve 1&2.
        state.phase = "baseline_chamber_v1v2"
        set_mux_sample(mux, logger, start_time, state, "1,2", "10 min chamber baseline; valve 3 bypass")
        acquire_for_duration(
            li850=li850,
            mfc=mfc,
            logger=logger,
            start_time=start_time,
            state=state,
            duration_s=args.baseline_chamber_s,
            note="chamber baseline; sample valve 1&2",
        )

        # 2. Room baseline, valve 3.
        state.phase = "baseline_room_v3"
        set_mux_sample(mux, logger, start_time, state, "3", "10 min room baseline; valve 1&2 bypass")
        acquire_for_duration(
            li850=li850,
            mfc=mfc,
            logger=logger,
            start_time=start_time,
            state=state,
            duration_s=args.baseline_room_s,
            note="room baseline; sample valve 3",
        )

        # 3. Pre-injection stabilisation.
        state.phase = "pre_injection_stabilization_v1v2"
        set_mux_sample(mux, logger, start_time, state, "1,2", "2 min stabilisation before CO2 injection")
        acquire_for_duration(
            li850=li850,
            mfc=mfc,
            logger=logger,
            start_time=start_time,
            state=state,
            duration_s=args.pre_injection_stabilize_s,
            note="pre-injection chamber stabilisation",
        )

        # 4. Injection.
        state.phase = "co2_injection_v1v2"
        state.injection_start_time = time.time()
        set_mfc_flow(
            mfc,
            logger,
            start_time,
            state,
            args.injection_flow,
            "mfc_on_command",
            f"Start injection to target {args.target_co2:.3f} ppm",
        )
        injection_result, final_co2 = acquire_injection_until_target(
            li850=li850,
            mfc=mfc,
            logger=logger,
            start_time=start_time,
            state=state,
            target_co2_ppm=args.target_co2,
            safety_cutoff_ppm=args.safety_cutoff,
            max_duration_s=args.injection_max_duration_s,
        )

        state.injection_stop_time = time.time()
        set_mfc_flow(
            mfc,
            logger,
            start_time,
            state,
            0.0,
            "mfc_off_command",
            f"Injection result={injection_result}; final_CO2={final_co2}",
        )

        if injection_result == "timeout":
            raise RuntimeError("Injection timeout. MFC has been set to zero; raw data were saved.")
        if injection_result == "safety_cutoff":
            raise RuntimeError("Injection safety cutoff reached. MFC has been set to zero; raw data were saved.")

        # 5. Decay loop: 10 min chamber, 2 min room.
        state.phase = "decay_loop"
        decay_start = time.time()
        logger.log_event(
            start_time=start_time,
            event="decay_start",
            phase=state.phase,
            sample=state.sample,
            mfc_setpoint=state.mfc_setpoint,
            mfc_actual=state.last_mfc_actual,
            details=f"Starting {args.decay_hours:.3f} h decay loop",
        )

        decay_end = decay_start + args.decay_hours * 3600.0
        cycle = 0
        while time.time() < decay_end:
            cycle += 1
            state.phase = "decay_chamber_v1v2"
            set_mux_sample(mux, logger, start_time, state, "1,2", f"decay cycle {cycle}; chamber sample")
            chamber_duration = min(args.decay_chamber_sample_s, max(0.0, decay_end - time.time()))
            if chamber_duration > 0:
                acquire_for_duration(
                    li850=li850,
                    mfc=mfc,
                    logger=logger,
                    start_time=start_time,
                    state=state,
                    duration_s=chamber_duration,
                    note=f"decay cycle {cycle}; chamber sample valve 1&2",
                )

            if time.time() >= decay_end:
                break

            state.phase = "decay_room_v3"
            set_mux_sample(mux, logger, start_time, state, "3", f"decay cycle {cycle}; room check")
            room_duration = min(args.decay_room_sample_s, max(0.0, decay_end - time.time()))
            if room_duration > 0:
                acquire_for_duration(
                    li850=li850,
                    mfc=mfc,
                    logger=logger,
                    start_time=start_time,
                    state=state,
                    duration_s=room_duration,
                    note=f"decay cycle {cycle}; room sample valve 3",
                )

        logger.log_event(
            start_time=start_time,
            event="decay_complete",
            phase=state.phase,
            sample=state.sample,
            mfc_setpoint=state.mfc_setpoint,
            mfc_actual=state.last_mfc_actual,
            details="Requested decay duration completed",
        )

    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C received. Saving raw data and setting MFC to zero.")
        try:
            logger.log_event(
                start_time=start_time,
                event="keyboard_interrupt",
                phase=state.phase,
                sample=state.sample,
                mfc_setpoint=state.mfc_setpoint,
                mfc_actual=state.last_mfc_actual,
                details="User interrupted run",
            )
        except Exception:
            pass
    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        try:
            logger.log_event(
                start_time=start_time,
                event="run_error",
                phase=state.phase,
                sample=state.sample,
                mfc_setpoint=state.mfc_setpoint,
                mfc_actual=state.last_mfc_actual,
                details=str(exc),
            )
        except Exception:
            pass
        raise
    finally:
        if mfc is not None:
            try:
                state.phase = "exit"
                set_mfc_flow(mfc, logger, start_time, state, 0.0, "mfc_zero_on_exit", "Final safety zero")
            except Exception:
                try:
                    mfc.close_zero()
                except Exception:
                    pass
        if mux is not None:
            try:
                mux.set_sample("1,2,3")
                logger.log_event(
                    start_time=start_time,
                    event="mux_return_all_to_li850",
                    phase="exit",
                    sample="1,2,3",
                    mfc_setpoint=0.0,
                    mfc_actual=state.last_mfc_actual,
                    details="Returned multiplexer to 1,2,3 before exit",
                )
            except Exception:
                pass
            mux.close()
        if li850 is not None:
            li850.close()
        logger.log_event(
            start_time=start_time,
            event="run_end",
            phase="exit",
            sample=state.sample,
            mfc_setpoint=0.0,
            mfc_actual=state.last_mfc_actual,
            details="Raw data acquisition ended",
        )
        logger.close()
        print("\n[EXIT] Done.")
        print(f"Raw CSV:    {logger.raw_path}")
        print(f"Events CSV: {logger.events_path}")
        print(f"Folder:     {out_dir}")


if __name__ == "__main__":
    main()
