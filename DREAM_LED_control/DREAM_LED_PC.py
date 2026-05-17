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
#   7. Full DREAM LED run routine
#
# run_dream_led.py should only contain user settings and call run_dream_led_system().

import serial
import serial.tools.list_ports
import time
from datetime import datetime


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

        # device_id -> serial.Serial object
        self.devices = {}

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

        # Give MicroPython USB serial time to settle after opening.
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
        """
        Automatic scan mode.

        This tries every available COM port and sends ID.
        Use this only when you do not know the COM numbers.
        For stable chamber operation, manual COM mode is usually better.
        """
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
        """
        Manual COM-port mode.

        manual_ports format:

        {
            "WHITE_LED_RP2040_1": "COM7",
            "WHITE_LED_RP2040_2": "COM8",
            "RED_LED_RP2040": "COM16",
        }
        """
        print("\nConnecting to manually selected DREAM LED ports...\n")

        for expected_id, port in manual_ports.items():
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
        """
        Send one command to one serial object.

        Returns:
            list[str]: reply lines

        If communication fails, returns an ERR line instead of crashing.
        """
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

            # Second short read window for MicroPython USB latency.
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

        # Use list(...) because failed devices may be removed during loop.
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

    def set_channel(self, device_id, dac_addr, channel, percent):
        command = f"SET {dac_addr} {channel} {percent}"
        return self.send(device_id, command)

    def set_all_channels_on_dac(self, device_id, dac_addr, percent):
        command = f"SET {dac_addr} ALL {percent}"
        return self.send(device_id, command)

    def set_all_on_device(self, device_id, percent):
        command = f"SETALL {percent}"
        return self.send(device_id, command)

    def set_all_connected(self, percent):
        return self.send_all_connected(f"SETALL {percent}")

    def ramp_channel(
        self,
        device_id,
        dac_addr,
        channel,
        start_percent,
        end_percent,
        duration_s,
    ):
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
    # Diagnostics: heartbeat and I2C scan
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
        """
        mode:
            "ON"
            "OFF"
            None
        """
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

        command = (
            f"TIME {now.year} {now.month} {now.day} "
            f"{now.hour} {now.minute} {now.second}"
        )

        return self.send(device_id, command)

    def set_time_from_pc_all_connected(self):
        for device_id in list(self.devices.keys()):
            self.set_time_from_pc(device_id)

    def set_schedule(self, device_id, on_time="08:00", off_time="20:00"):
        command = f"SCHEDULE {on_time} {off_time}"
        return self.send(device_id, command)

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
        """
        settings_table format:

        [
            ("WHITE_LED_RP2040_1", "0x60", "A", 12.5),
            ("WHITE_LED_RP2040_1", "0x60", "B", 18.0),
            ("RED_LED_RP2040",     "0x61", "C", 30.0),
        ]
        """
        for row in settings_table:
            device_id, dac_addr, channel, percent = row

            if device_id not in self.devices:
                print(f"Skipped {device_id}: not connected")
                continue

            self.set_channel(device_id, dac_addr, channel, percent)

    def apply_nested_settings(self, settings):
        """
        settings format:

        {
            "WHITE_LED_RP2040_1": {
                "0x60": {"A": 10, "B": 10, "C": 15, "D": 15},
                "0x61": {"A": 20, "B": 20, "C": 25, "D": 25},
            },
            "RED_LED_RP2040": {
                "0x60": {"A": 30, "B": 30, "C": 40, "D": 40},
            },
        }
        """
        for device_id, dac_block in settings.items():
            if device_id not in self.devices:
                print(f"Skipped {device_id}: not connected")
                continue

            for dac_addr, channel_block in dac_block.items():
                for channel, percent in channel_block.items():
                    self.set_channel(device_id, dac_addr, channel, percent)


# ==========================================================
# Top-level DREAM LED workflow functions
# ==========================================================

def configure_led_system(
    led,
    led_settings,
    light_on_time="08:00",
    light_off_time="20:00",
    heartbeat_after_config="ON",
    scan_i2c_after_connect=True,
):
    """
    Configure all connected LED RP2040 controllers.

    Steps:
        1. Optional I2C scan
        2. Set time from PC
        3. Set light schedule
        4. Enable schedule
        5. Apply per-channel LED settings
        6. Set heartbeat mode
        7. Read status once
    """
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


def monitor_led_status(
    led,
    status_interval_s=60,
):
    """
    Optional monitoring loop.
    The LED schedule is already running on the RP2040 controllers.
    This loop only checks status.
    """
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
    """
    Full DREAM LED control routine.

    This is the only function that run_dream_led.py needs to call.
    """
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
            monitor_led_status(
                led=led,
                status_interval_s=status_interval_s,
            )
        else:
            print("\nKEEP_MONITORING = False")
            print("PC script will now close cleanly.")
            print("RP2040 controllers will continue running their schedule until reboot or power loss.")

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