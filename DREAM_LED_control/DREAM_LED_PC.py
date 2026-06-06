# DREAM_LED_PC.py
# PC-side Python helper and controller for DREAM LED RP2040 system
#
# Requires:
#   py -m pip install pyserial
#
# This file contains:
#   1. Serial connection helpers
#   2. Manual COM-port connection
#   3. Optional automatic scan
#   4. Per-channel LED control
#   5. Time and schedule setup
#   6. Heartbeat and I2C diagnostics
#   7. Fixed and diurnal lighting-regime helpers
#   8. Full DREAM LED run routine

import math
import serial
import serial.tools.list_ports
import time
from datetime import datetime


WHITE_KEYWORDS = ("WHITE",)
RED_KEYWORDS = ("RED",)
UVIR_KEYWORDS = ("UVIR", "UV", "IR")


class DreamLEDPC:
    def __init__(
        self,
        expected_devices=None,
        baudrate=115200,
        connect_delay_s=2.0,
        command_delay_s=0.20,
        read_extra_s=0.20,
    ):
        if expected_devices is None:
            expected_devices = [
                "WHITE_LED_RP2040_1",
                "WHITE_LED_RP2040_2",
                "RED_LED_RP2040",
                "UVIR_LED_RP2040_1",
                "UVIR_LED_RP2040_2",
            ]

        self.expected_devices = expected_devices
        self.baudrate = baudrate
        self.connect_delay_s = connect_delay_s
        self.command_delay_s = command_delay_s
        self.read_extra_s = read_extra_s
        self.devices = {}  # device_id -> serial.Serial object

    # ==========================================================
    # Serial connection
    # ==========================================================

    def list_ports(self):
        ports = list(serial.tools.list_ports.comports())
        print("Available serial ports:")
        if not ports:
            print("  None detected")
        for p in ports:
            print(f"  {p.device} | {p.description}")
        return ports

    def open_serial(self, port):
        ser = serial.Serial(
            port=port,
            baudrate=self.baudrate,
            timeout=1,
            write_timeout=2,
            dsrdtr=False,
            rtscts=False,
        )
        time.sleep(self.connect_delay_s)
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
        except Exception:
            pass
        return ser

    def parse_id_reply(self, replies):
        for line in replies:
            line = line.strip()
            if line.startswith("ID,"):
                return line.split(",", 1)[1].strip()
        return None

    def print_connected_devices(self):
        print("\nConnected devices:")
        if not self.devices:
            print("  None")
        else:
            for device_id, ser in self.devices.items():
                print(f"  {device_id} on {ser.port}")

    def scan_and_connect(self):
        ports = list(serial.tools.list_ports.comports())
        print("\nScanning for DREAM LED RP2040 controllers...\n")
        for p in ports:
            port = p.device
            try:
                ser = self.open_serial(port)
                replies = self.send_raw(ser, "ID")
                device_id = self.parse_id_reply(replies)
                if device_id in self.expected_devices:
                    self.devices[device_id] = ser
                    print(f"Connected: {device_id} on {port}")
                else:
                    print(f"Skipped: {port} replied {replies}")
                    ser.close()
            except Exception as e:
                print(f"Skipped: {port} ({e})")
        self.print_connected_devices()
        return self.devices

    def connect_manual_ports(self, manual_ports):
        print("\nConnecting to manually selected DREAM LED ports...\n")
        for expected_id, port in manual_ports.items():
            if not str(port).strip():
                continue
            try:
                ser = self.open_serial(port)
                replies = self.send_raw(ser, "ID")
                detected_id = self.parse_id_reply(replies)
                if detected_id == expected_id:
                    self.devices[expected_id] = ser
                    print(f"Connected: {expected_id} on {port}")
                else:
                    print(f"Wrong or no reply on {port}")
                    print(f"  Expected: {expected_id}")
                    print(f"  Got replies: {replies}")
                    ser.close()
            except Exception as e:
                print(f"Could not open {expected_id} on {port}: {e}")
        self.print_connected_devices()
        return self.devices

    def close(self):
        for name, ser in list(self.devices.items()):
            try:
                if ser and ser.is_open:
                    ser.close()
                print(f"Closed {name}")
            except Exception:
                pass
        self.devices.clear()

    # ==========================================================
    # Safe command functions
    # ==========================================================

    def send_raw(self, ser, command):
        try:
            if ser is None:
                return ["ERR,serial object is None"]
            if not ser.is_open:
                return ["ERR,serial port closed"]
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            ser.write((command + "\n").encode("utf-8"))
            ser.flush()
            time.sleep(self.command_delay_s)
            replies = []
            while ser.in_waiting:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    replies.append(line)
            time.sleep(self.read_extra_s)
            while ser.in_waiting:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    replies.append(line)
            return replies
        except Exception as e:
            return [f"ERR,serial write/read failed: {e}"]

    def send(self, device_id, command, quiet=False, remove_on_error=True):
        if device_id not in self.devices:
            if not quiet:
                print(f"Not connected: {device_id}")
            return []
        ser = self.devices[device_id]
        replies = self.send_raw(ser, command)
        if not quiet:
            print(f"{device_id} >>> {command}")
            for r in replies:
                print(f"  {r}")
        failed = any(str(r).startswith("ERR,serial") for r in replies)
        if failed and remove_on_error:
            print(f"Serial connection failed for {device_id}; removing from active devices.")
            try:
                ser.close()
            except Exception:
                pass
            self.devices.pop(device_id, None)
        return replies

    def send_all_connected(self, command, quiet=False, remove_on_error=True):
        results = {}
        for device_id in list(self.devices.keys()):
            results[device_id] = self.send(
                device_id,
                command,
                quiet=quiet,
                remove_on_error=remove_on_error,
            )
        return results

    # ==========================================================
    # High-level LED control
    # ==========================================================

    def set_channel(self, device_id, dac_addr, channel, percent, quiet=False):
        percent = self._clamp_percent(percent)
        command = f"SET {dac_addr} {channel} {percent}"
        return self.send(device_id, command, quiet=quiet)

    def set_all_channels_on_dac(self, device_id, dac_addr, percent, quiet=False):
        percent = self._clamp_percent(percent)
        command = f"SET {dac_addr} ALL {percent}"
        return self.send(device_id, command, quiet=quiet)

    def set_all_on_device(self, device_id, percent, quiet=False):
        percent = self._clamp_percent(percent)
        command = f"SETALL {percent}"
        return self.send(device_id, command, quiet=quiet)

    def set_all_connected(self, percent):
        percent = self._clamp_percent(percent)
        return self.send_all_connected(f"SETALL {percent}")

    def ramp_channel(self, device_id, dac_addr, channel, start_percent, end_percent, duration_s):
        command = f"RAMP {dac_addr} {channel} {start_percent} {end_percent} {duration_s}"
        return self.send(device_id, command)

    def on(self, device_id):
        return self.send(device_id, "ON")

    def off(self, device_id):
        return self.send(device_id, "OFF")

    def on_all_connected(self):
        return self.send_all_connected("ON")

    def off_all_connected(self):
        return self.send_all_connected("OFF")

    def status(self, device_id):
        return self.send(device_id, "STATUS")

    def status_all_connected(self):
        return self.send_all_connected("STATUS")

    # ==========================================================
    # Diagnostics
    # ==========================================================

    def heartbeat_on(self, device_id):
        return self.send(device_id, "HEARTBEAT_ON")

    def heartbeat_off(self, device_id):
        return self.send(device_id, "HEARTBEAT_OFF")

    def heartbeat_on_all_connected(self):
        return self.send_all_connected("HEARTBEAT_ON")

    def heartbeat_off_all_connected(self):
        return self.send_all_connected("HEARTBEAT_OFF")

    def set_heartbeat_all_connected(self, mode):
        if mode is None:
            return {}
        mode = str(mode).upper()
        if mode == "ON":
            return self.heartbeat_on_all_connected()
        if mode == "OFF":
            return self.heartbeat_off_all_connected()
        print(f"Unknown heartbeat mode: {mode}. Use 'ON', 'OFF', or None.")
        return {}

    def i2c_scan(self, device_id):
        return self.send(device_id, "I2C_SCAN")

    def i2c_scan_all_connected(self):
        return self.send_all_connected("I2C_SCAN")

    # ==========================================================
    # Time and schedule
    # ==========================================================

    def set_time_from_pc(self, device_id):
        now = datetime.now()
        command = f"TIME {now.year} {now.month} {now.day} {now.hour} {now.minute} {now.second}"
        return self.send(device_id, command)

    def set_time_from_pc_all_connected(self):
        for device_id in list(self.devices.keys()):
            self.set_time_from_pc(device_id)

    def set_schedule(self, device_id, on_time="08:00", off_time="20:00"):
        return self.send(device_id, f"SCHEDULE {on_time} {off_time}")

    def set_schedule_all_connected(self, on_time="08:00", off_time="20:00"):
        for device_id in list(self.devices.keys()):
            self.set_schedule(device_id, on_time, off_time)

    def schedule_on(self, device_id):
        return self.send(device_id, "SCHEDULE_ON")

    def schedule_off(self, device_id):
        return self.send(device_id, "SCHEDULE_OFF")

    def schedule_on_all_connected(self):
        return self.send_all_connected("SCHEDULE_ON")

    def schedule_off_all_connected(self):
        return self.send_all_connected("SCHEDULE_OFF")

    # ==========================================================
    # Apply calibration/settings table
    # ==========================================================

    def apply_settings_table(self, settings_table):
        for device_id, dac_addr, channel, percent in settings_table:
            if device_id not in self.devices:
                print(f"Skipped {device_id}: not connected")
                continue
            self.set_channel(device_id, dac_addr, channel, percent)

    def apply_nested_settings(self, settings):
        for device_id, dac_block in settings.items():
            if device_id not in self.devices:
                print(f"Skipped {device_id}: not connected")
                continue
            for dac_addr, channel_block in dac_block.items():
                for channel, percent in channel_block.items():
                    self.set_channel(device_id, dac_addr, channel, percent)

    # ==========================================================
    # Lighting regime helpers
    # ==========================================================

    @staticmethod
    def _clamp_percent(value):
        value = float(value)
        if value < 0:
            return 0.0
        if value > 100:
            return 100.0
        return round(value, 3)

    @staticmethod
    def _parse_hhmm(text):
        h, m = str(text).split(":")
        h = int(h)
        m = int(m)
        if h < 0 or h > 23 or m < 0 or m > 59:
            raise ValueError("time must be HH:MM")
        return h * 60 + m

    @staticmethod
    def _device_group(device_id):
        upper = str(device_id).upper()
        if any(k in upper for k in WHITE_KEYWORDS):
            return "white"
        if any(k in upper for k in RED_KEYWORDS):
            return "red"
        if any(k in upper for k in UVIR_KEYWORDS):
            return "uvir"
        return "other"

    def in_light_period(self, now_dt, on_time, off_time):
        now_min = now_dt.hour * 60 + now_dt.minute + now_dt.second / 60.0
        on_min = self._parse_hhmm(on_time)
        off_min = self._parse_hhmm(off_time)
        if on_min < off_min:
            return on_min <= now_min < off_min
        return now_min >= on_min or now_min < off_min

    def diurnal_factor(self, now_dt, on_time, off_time, curve_power=1.0):
        """
        Return 0-1 half-sine diurnal factor.
        0 at light_on, 1 at midpoint, 0 at light_off.
        curve_power >1 narrows the midday peak; <1 broadens shoulders.
        """
        now_min = now_dt.hour * 60 + now_dt.minute + now_dt.second / 60.0
        on_min = self._parse_hhmm(on_time)
        off_min = self._parse_hhmm(off_time)
        if on_min == off_min:
            return 0.0
        if on_min < off_min:
            if not (on_min <= now_min < off_min):
                return 0.0
            x = (now_min - on_min) / (off_min - on_min)
        else:
            # Cross-midnight regime.
            duration = (1440 - on_min) + off_min
            if now_min >= on_min:
                x = (now_min - on_min) / duration
            elif now_min < off_min:
                x = ((1440 - on_min) + now_min) / duration
            else:
                return 0.0
        x = max(0.0, min(1.0, x))
        power = max(0.1, float(curve_power))
        return math.sin(math.pi * x) ** power

    def apply_fixed_regime(self, led_settings, on_time="08:00", off_time="20:00"):
        """
        Fixed mode: set PC time, set local RP2040 schedule, and apply current channel settings.
        The RP2040 can keep running this fixed regime after the PC closes.
        """
        self.set_time_from_pc_all_connected()
        self.set_schedule_all_connected(on_time, off_time)
        self.schedule_on_all_connected()
        self.apply_nested_settings(led_settings)

    def apply_scaled_settings(
        self,
        base_settings,
        factor=1.0,
        white_max_scale=100.0,
        red_max_scale=100.0,
        red_white_ratio=1.0,
        include_uvir=False,
        quiet=True,
    ):
        """
        Apply a scaled lighting state.

        base_settings are the channel percentages shown in the GUI. They are treated as
        the channel-level maximum values. The diurnal factor scales them from 0 to max.

        White actual = channel_base * factor * white_max_scale/100
        Red actual   = channel_base * factor * red_max_scale/100 * red_white_ratio
        UV/IR actual = channel_base * factor only if include_uvir=True, otherwise unchanged/skipped
        """
        factor = max(0.0, min(1.0, float(factor)))
        white_multiplier = max(0.0, float(white_max_scale)) / 100.0
        red_multiplier = max(0.0, float(red_max_scale)) / 100.0 * max(0.0, min(5.0, float(red_white_ratio)))

        for device_id, dac_block in base_settings.items():
            if device_id not in self.devices:
                continue
            group = self._device_group(device_id)
            if group == "white":
                group_multiplier = white_multiplier
            elif group == "red":
                group_multiplier = red_multiplier
            elif group == "uvir":
                if not include_uvir:
                    continue
                group_multiplier = 1.0
            else:
                group_multiplier = 1.0

            for dac_addr, channel_block in dac_block.items():
                for channel, base_percent in channel_block.items():
                    target = self._clamp_percent(float(base_percent) * factor * group_multiplier)
                    self.set_channel(device_id, dac_addr, channel, target, quiet=quiet)

    def apply_diurnal_now(
        self,
        base_settings,
        on_time="08:00",
        off_time="20:00",
        curve_power=1.0,
        white_max_scale=100.0,
        red_max_scale=100.0,
        red_white_ratio=1.0,
        include_uvir=False,
        now_dt=None,
        quiet=True,
    ):
        """
        Compute the current diurnal factor and apply one dynamic light state.
        Returns the factor used.
        """
        if now_dt is None:
            now_dt = datetime.now()
        factor = self.diurnal_factor(now_dt, on_time, off_time, curve_power=curve_power)
        if factor <= 0:
            # Outside light period: turn off connected white/red devices. Keep UVIR off unless explicitly included.
            for device_id in list(self.devices.keys()):
                group = self._device_group(device_id)
                if group in ("white", "red") or (include_uvir and group == "uvir"):
                    self.set_all_on_device(device_id, 0.0, quiet=quiet)
            return 0.0
        self.apply_scaled_settings(
            base_settings,
            factor=factor,
            white_max_scale=white_max_scale,
            red_max_scale=red_max_scale,
            red_white_ratio=red_white_ratio,
            include_uvir=include_uvir,
            quiet=quiet,
        )
        return factor


# ==========================================================
# Top-level workflow functions
# ==========================================================

def configure_led_system(
    led,
    led_settings,
    light_on_time="08:00",
    light_off_time="20:00",
    heartbeat_after_config="ON",
    scan_i2c_after_connect=True,
):
    if scan_i2c_after_connect:
        print("\nStep 1: I2C scan on connected RP2040 controllers")
        led.i2c_scan_all_connected()
    else:
        print("\nStep 1: I2C scan skipped")

    print("\nStep 2: Update RP2040 time from PC")
    led.set_time_from_pc_all_connected()

    print("\nStep 3: Set light schedule")
    led.set_schedule_all_connected(light_on_time, light_off_time)

    print("\nStep 4: Enable schedule")
    led.schedule_on_all_connected()

    print("\nStep 5: Apply LED fine-tuning settings")
    led.apply_nested_settings(led_settings)

    print("\nStep 6: Set heartbeat mode")
    led.set_heartbeat_all_connected(heartbeat_after_config)

    print("\nStep 7: Read status once")
    led.status_all_connected()


def monitor_led_status(led, status_interval_s=60):
    print("\nMonitoring enabled.")
    print(f"Status interval: {status_interval_s} s")
    print("Press Ctrl+C to stop monitoring.\n")
    while True:
        time.sleep(status_interval_s)
        print("\n--- Status check ---")
        try:
            led.status_all_connected()
        except Exception as e:
            print(f"Status check failed, but LED schedule was already configured: {e}")
        if not led.devices:
            print("No active LED controllers remain connected.")
            print("Stopping monitor loop.")
            break


def run_dream_led_system(
    expected_devices,
    manual_ports,
    led_settings,
    light_on_time="08:00",
    light_off_time="20:00",
    baudrate=115200,
    connect_delay_s=2.0,
    command_delay_s=0.20,
    read_extra_s=0.20,
    use_manual_ports=True,
    keep_monitoring=False,
    status_interval_s=60,
    turn_off_on_ctrl_c=False,
    heartbeat_after_config="ON",
    scan_i2c_after_connect=True,
):
    led = DreamLEDPC(
        expected_devices=expected_devices,
        baudrate=baudrate,
        connect_delay_s=connect_delay_s,
        command_delay_s=command_delay_s,
        read_extra_s=read_extra_s,
    )
    try:
        print("\n========== DREAM LED CONTROL ==========\n")
        led.list_ports()
        if use_manual_ports:
            led.connect_manual_ports(manual_ports)
        else:
            led.scan_and_connect()
        if not led.devices:
            print("\nNo DREAM LED controllers connected.")
            print("Check:")
            print("  1. Thonny is fully closed")
            print("  2. Each Pico has the correct DEVICE_ID")
            print("  3. main.py is saved on each Pico")
            print("  4. DREAM_LED_RP2040.py filename is correct")
            print("  5. The COM port numbers are correct")
            return
        configure_led_system(
            led=led,
            led_settings=led_settings,
            light_on_time=light_on_time,
            light_off_time=light_off_time,
            heartbeat_after_config=heartbeat_after_config,
            scan_i2c_after_connect=scan_i2c_after_connect,
        )
        print("\nDREAM LED system configured.")
        print(f"Schedule active: {light_on_time}-{light_off_time}.")
        print("Connected RP2040 controllers have received their target channel settings.")
        if keep_monitoring:
            monitor_led_status(led=led, status_interval_s=status_interval_s)
        else:
            print("\nKEEP_MONITORING = False")
            print("PC script will now close cleanly.")
            print("RP2040 controllers will continue running their fixed schedule until reboot or power loss.")
    except KeyboardInterrupt:
        print("\nStopped by user.")
        if turn_off_on_ctrl_c:
            print("Turning off all connected LEDs...")
            led.off_all_connected()
        else:
            print("Leaving RP2040 controllers running their schedule.")
    finally:
        led.close()
        print("\nDone.")
