# DREAM_LED_RP2040.py
# MicroPython helper for DREAM LED RP2040 controllers
#
# Runs on Raspberry Pi Pico / RP2040.
# Receives USB serial commands from PC.
#
# Supported commands:
#   ID
#   STATUS
#   ON
#   OFF
#   SET 0x60 A 25
#   SET 0x60 ALL 25
#   SETALL 10
#   RAMP 0x60 A 0 100 60
#   RAMP 0x60 ALL 0 100 60
#   SCHEDULE 08:00 20:00
#   SCHEDULE_ON
#   SCHEDULE_OFF
#   TIME 2026 5 2 14 30 0
#   HEARTBEAT_ON
#   HEARTBEAT_OFF
#   HELP
#
# Main improvements:
#   1. Safe I2C write with retry
#   2. I2C error counter instead of crashing
#   3. Heartbeat output for diagnosing resets/disconnects
#   4. Better STATUS output
#   5. Schedule remains local on the RP2040 after PC setup

from machine import Pin, I2C  # type: ignore
import time
import sys
import select


# ==========================================================
# MicroPython-compatible timing helpers
# ==========================================================

def now_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def diff_ms(new, old):
    try:
        return time.ticks_diff(new, old)
    except AttributeError:
        return new - old


class DreamLEDController:
    def __init__(
        self,
        device_id,
        i2c_id=0,
        sda_pin=20,
        scl_pin=21,
        pulse_pin=2,
        dac_addrs=(0x60, 0x61, 0x62),
        full_scale=4095,
        i2c_freq=100000,
        i2c_retries=2,
        heartbeat_enabled=True,
        heartbeat_interval_s=30,
    ):
        self.device_id = device_id
        self.full_scale = full_scale
        self.dac_addrs = list(dac_addrs)
        self.i2c_retries = int(i2c_retries)

        self.channel_index = {
            "A": 0,
            "B": 1,
            "C": 2,
            "D": 3,
        }

        self.pulse = Pin(pulse_pin, Pin.OUT)
        self.pulse.off()

        self.i2c = I2C(
            i2c_id,
            sda=Pin(sda_pin),
            scl=Pin(scl_pin),
            freq=i2c_freq,
        )

        self.i2c_id = i2c_id
        self.sda_pin = sda_pin
        self.scl_pin = scl_pin
        self.pulse_pin = pulse_pin
        self.i2c_freq = i2c_freq

        self.dac_values = {}
        self.target_percent = {}

        for addr in self.dac_addrs:
            self.dac_values[addr] = [0, 0, 0, 0]
            self.target_percent[addr] = [0.0, 0.0, 0.0, 0.0]

        self.schedule_enabled = True
        self.light_on_hour = 8
        self.light_on_minute = 0
        self.light_off_hour = 20
        self.light_off_minute = 0

        self.manual_time_enabled = False
        self.manual_datetime = [2000, 1, 1, 0, 0, 0]
        self.manual_set_ms = now_ms()

        self.last_schedule_state = None

        self.heartbeat_enabled = heartbeat_enabled
        self.heartbeat_interval_ms = int(heartbeat_interval_s * 1000)
        self.last_heartbeat_ms = now_ms()

        self.loop_counter = 0
        self.i2c_error_count = 0
        self.last_i2c_error = "None"
        self.last_command = "None"

        # Initial safe state
        self.safe_all_outputs_zero()

    # ==========================================================
    # Time functions
    # ==========================================================

    def set_manual_time(self, year, month, day, hour, minute, second):
        self.manual_time_enabled = True
        self.manual_datetime = [
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
        ]
        self.manual_set_ms = now_ms()

    def get_now_hms(self):
        if not self.manual_time_enabled:
            # If time has not been set from PC, use localtime.
            # On Pico without RTC battery this is not reliable after reboot.
            t = time.localtime()
            return int(t[3]), int(t[4]), int(t[5])

        elapsed_s = diff_ms(now_ms(), self.manual_set_ms) // 1000

        year, month, day, hour, minute, second = self.manual_datetime

        total = hour * 3600 + minute * 60 + second + elapsed_s
        total = total % 86400

        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60

        return int(h), int(m), int(s)

    def time_to_minutes(self, hour, minute):
        return int(hour) * 60 + int(minute)

    def is_light_period(self):
        h, m, s = self.get_now_hms()
        now_min = self.time_to_minutes(h, m)
        on_min = self.time_to_minutes(self.light_on_hour, self.light_on_minute)
        off_min = self.time_to_minutes(self.light_off_hour, self.light_off_minute)

        if on_min < off_min:
            return on_min <= now_min < off_min

        # For schedules crossing midnight
        return now_min >= on_min or now_min < off_min

    # ==========================================================
    # I2C and DAC functions
    # ==========================================================

    def scan_i2c(self):
        try:
            return self.i2c.scan()
        except Exception as e:
            self.i2c_error_count += 1
            self.last_i2c_error = "scan failed: {}".format(e)
            return []

    def percent_to_dac(self, percent):
        percent = float(percent)

        if percent < 0:
            percent = 0.0

        if percent > 100:
            percent = 100.0

        return int(self.full_scale * percent / 100.0)

    def dac_to_percent(self, value):
        return 100.0 * float(value) / float(self.full_scale)

    def safe_i2c_writeto(self, addr, data):
        """
        Safe I2C write with retry.
        Returns True if successful, False if failed.
        """
        for attempt in range(self.i2c_retries + 1):
            try:
                self.i2c.writeto(addr, data)
                return True

            except OSError as e:
                self.i2c_error_count += 1
                self.last_i2c_error = "addr={}, attempt={}, error={}".format(
                    hex(addr),
                    attempt + 1,
                    e,
                )

                # Short delay before retry
                time.sleep(0.02)

            except Exception as e:
                self.i2c_error_count += 1
                self.last_i2c_error = "addr={}, attempt={}, error={}".format(
                    hex(addr),
                    attempt + 1,
                    e,
                )
                time.sleep(0.02)

        print(
            "ERR,{},I2C_WRITE_FAILED,{},{}".format(
                self.device_id,
                hex(addr),
                self.last_i2c_error,
            )
        )

        return False

    def write_mcp4728_fast(self, addr, values):
        """
        MCP4728 fast-write.
        Writes all four DAC channels.
        Does not write EEPROM.
        Safe for repeated light control.
        """
        data = bytearray()

        for v in values:
            v = int(v)

            if v < 0:
                v = 0

            if v > self.full_scale:
                v = self.full_scale

            high = (v >> 8) & 0x0F
            low = v & 0xFF

            data.append(high)
            data.append(low)

        return self.safe_i2c_writeto(addr, data)

    def apply_addr(self, addr):
        return self.write_mcp4728_fast(addr, self.dac_values[addr])

    def apply_all(self):
        ok = True

        for addr in self.dac_addrs:
            if not self.apply_addr(addr):
                ok = False

        return ok

    def safe_all_outputs_zero(self):
        """
        Set all known DAC values to zero.
        Does not raise if an I2C device is temporarily unavailable.
        """
        for addr in self.dac_addrs:
            self.dac_values[addr] = [0, 0, 0, 0]
            self.write_mcp4728_fast(addr, self.dac_values[addr])

        self.pulse.off()

    def set_channel(self, addr, channel, percent):
        if addr not in self.dac_addrs:
            raise ValueError("Unknown DAC address: {}".format(hex(addr)))

        channel = channel.upper()
        value = self.percent_to_dac(percent)

        if channel == "ALL":
            for i in range(4):
                self.dac_values[addr][i] = value
                self.target_percent[addr][i] = float(percent)

        else:
            if channel not in self.channel_index:
                raise ValueError("Unknown channel: {}".format(channel))

            idx = self.channel_index[channel]
            self.dac_values[addr][idx] = value
            self.target_percent[addr][idx] = float(percent)

        self.pulse.on()
        return self.apply_addr(addr)

    def set_all_percent(self, percent):
        self.pulse.on()

        value = self.percent_to_dac(percent)
        ok = True

        for addr in self.dac_addrs:
            for i in range(4):
                self.dac_values[addr][i] = value
                self.target_percent[addr][i] = float(percent)

            if not self.apply_addr(addr):
                ok = False

        return ok

    def all_off(self):
        for addr in self.dac_addrs:
            for i in range(4):
                self.dac_values[addr][i] = 0

            self.apply_addr(addr)

        self.pulse.off()

    def output_off_keep_targets(self):
        for addr in self.dac_addrs:
            self.dac_values[addr] = [0, 0, 0, 0]
            self.apply_addr(addr)

        self.pulse.off()

    def output_on_restore_targets(self):
        self.pulse.on()

        for addr in self.dac_addrs:
            for i in range(4):
                self.dac_values[addr][i] = self.percent_to_dac(
                    self.target_percent[addr][i]
                )

            self.apply_addr(addr)

    def ramp(self, addr, channel, start_percent, end_percent, duration_s):
        addr = int(addr)
        channel = channel.upper()

        duration_s = float(duration_s)

        if duration_s <= 0:
            self.set_channel(addr, channel, end_percent)
            return

        steps = max(1, int(duration_s * 5.0))  # 5 updates per second
        delay = duration_s / steps

        self.pulse.on()

        for i in range(steps + 1):
            p = float(start_percent) + (
                float(end_percent) - float(start_percent)
            ) * i / steps

            self.set_channel(addr, channel, p)
            time.sleep(delay)

    # ==========================================================
    # Schedule functions
    # ==========================================================

    def set_schedule(self, on_time, off_time):
        on_h, on_m = self.parse_hhmm(on_time)
        off_h, off_m = self.parse_hhmm(off_time)

        self.light_on_hour = on_h
        self.light_on_minute = on_m
        self.light_off_hour = off_h
        self.light_off_minute = off_m

    def parse_hhmm(self, txt):
        parts = txt.split(":")

        if len(parts) != 2:
            raise ValueError("Time must be HH:MM")

        h = int(parts[0])
        m = int(parts[1])

        if h < 0 or h > 23:
            raise ValueError("Hour must be 0-23")

        if m < 0 or m > 59:
            raise ValueError("Minute must be 0-59")

        return h, m

    def schedule_tick(self):
        if not self.schedule_enabled:
            return

        light_should_be_on = self.is_light_period()

        if self.last_schedule_state is None:
            self.last_schedule_state = light_should_be_on

            if light_should_be_on:
                self.output_on_restore_targets()
            else:
                self.output_off_keep_targets()

            return

        if light_should_be_on != self.last_schedule_state:
            self.last_schedule_state = light_should_be_on

            if light_should_be_on:
                self.output_on_restore_targets()
                print("SCHEDULE,{},LIGHT_ON".format(self.device_id))

            else:
                self.output_off_keep_targets()
                print("SCHEDULE,{},LIGHT_OFF".format(self.device_id))

    # ==========================================================
    # Serial command functions
    # ==========================================================

    def help_text(self):
        return (
            "COMMANDS: "
            "ID | STATUS | ON | OFF | "
            "SET 0x60 A 25 | SET 0x60 ALL 25 | SETALL 10 | "
            "RAMP 0x60 A 0 100 60 | "
            "SCHEDULE 08:00 20:00 | SCHEDULE_ON | SCHEDULE_OFF | "
            "TIME 2026 5 2 14 30 0 | "
            "HEARTBEAT_ON | HEARTBEAT_OFF | "
            "I2C_SCAN | HELP"
        )

    def status_text(self):
        h, m, s = self.get_now_hms()

        return (
            "STATUS,"
            "{device},"
            "time={h:02d}:{m:02d}:{s:02d},"
            "schedule={schedule},"
            "on={on_h:02d}:{on_m:02d},"
            "off={off_h:02d}:{off_m:02d},"
            "pulse={pulse},"
            "heartbeat={heartbeat},"
            "i2c_errors={i2c_errors},"
            "last_i2c_error={last_i2c_error},"
            "last_command={last_command},"
            "targets={targets}"
        ).format(
            device=self.device_id,
            h=h,
            m=m,
            s=s,
            schedule=self.schedule_enabled,
            on_h=self.light_on_hour,
            on_m=self.light_on_minute,
            off_h=self.light_off_hour,
            off_m=self.light_off_minute,
            pulse=self.pulse.value(),
            heartbeat=self.heartbeat_enabled,
            i2c_errors=self.i2c_error_count,
            last_i2c_error=self.last_i2c_error,
            last_command=self.last_command,
            targets=self.target_percent,
        )

    def heartbeat_text(self):
        h, m, s = self.get_now_hms()

        return (
            "HEARTBEAT,"
            "{device},"
            "time={h:02d}:{m:02d}:{s:02d},"
            "pulse={pulse},"
            "schedule={schedule},"
            "i2c_errors={i2c_errors}"
        ).format(
            device=self.device_id,
            h=h,
            m=m,
            s=s,
            pulse=self.pulse.value(),
            schedule=self.schedule_enabled,
            i2c_errors=self.i2c_error_count,
        )

    def handle_command(self, line):
        line = line.strip()

        if not line:
            return

        self.last_command = line

        parts = line.split()
        cmd = parts[0].upper()

        try:
            if cmd == "ID":
                print("ID,{}".format(self.device_id))

            elif cmd == "HELP":
                print(self.help_text())

            elif cmd == "STATUS":
                print(self.status_text())

            elif cmd == "I2C_SCAN":
                found = self.scan_i2c()
                found_hex = [hex(x) for x in found]
                print("I2C_SCAN,{},{}".format(self.device_id, found_hex))

            elif cmd == "ON":
                self.output_on_restore_targets()
                print("OK,{},ON".format(self.device_id))

            elif cmd == "OFF":
                self.output_off_keep_targets()
                print("OK,{},OFF".format(self.device_id))

            elif cmd == "SET":
                # SET 0x60 A 25
                if len(parts) != 4:
                    raise ValueError("Usage: SET 0x60 A 25")

                addr = int(parts[1], 16)
                channel = parts[2].upper()
                percent = float(parts[3])

                ok = self.set_channel(addr, channel, percent)

                if ok:
                    print(
                        "OK,{},SET,{},{},{}".format(
                            self.device_id,
                            hex(addr),
                            channel,
                            percent,
                        )
                    )
                else:
                    print(
                        "ERR,{},SET_FAILED,{},{},{}".format(
                            self.device_id,
                            hex(addr),
                            channel,
                            percent,
                        )
                    )

            elif cmd == "SETALL":
                # SETALL 10
                if len(parts) != 2:
                    raise ValueError("Usage: SETALL 10")

                percent = float(parts[1])
                ok = self.set_all_percent(percent)

                if ok:
                    print("OK,{},SETALL,{}".format(self.device_id, percent))
                else:
                    print("ERR,{},SETALL_FAILED,{}".format(self.device_id, percent))

            elif cmd == "RAMP":
                # RAMP 0x60 A 0 100 60
                if len(parts) != 6:
                    raise ValueError("Usage: RAMP 0x60 A 0 100 60")

                addr = int(parts[1], 16)
                channel = parts[2].upper()
                start_percent = float(parts[3])
                end_percent = float(parts[4])
                duration_s = float(parts[5])

                self.ramp(addr, channel, start_percent, end_percent, duration_s)

                print(
                    "OK,{},RAMP,{},{},{},{},{}".format(
                        self.device_id,
                        hex(addr),
                        channel,
                        start_percent,
                        end_percent,
                        duration_s,
                    )
                )

            elif cmd == "SCHEDULE":
                # SCHEDULE 08:00 20:00
                if len(parts) != 3:
                    raise ValueError("Usage: SCHEDULE 08:00 20:00")

                self.set_schedule(parts[1], parts[2])

                print(
                    "OK,{},SCHEDULE,{:02d}:{:02d},{:02d}:{:02d}".format(
                        self.device_id,
                        self.light_on_hour,
                        self.light_on_minute,
                        self.light_off_hour,
                        self.light_off_minute,
                    )
                )

            elif cmd == "SCHEDULE_ON":
                self.schedule_enabled = True
                self.last_schedule_state = None
                print("OK,{},SCHEDULE_ON".format(self.device_id))

            elif cmd == "SCHEDULE_OFF":
                self.schedule_enabled = False
                print("OK,{},SCHEDULE_OFF".format(self.device_id))

            elif cmd == "TIME":
                # TIME 2026 5 2 14 30 0
                if len(parts) != 7:
                    raise ValueError("Usage: TIME 2026 5 2 14 30 0")

                year = int(parts[1])
                month = int(parts[2])
                day = int(parts[3])
                hour = int(parts[4])
                minute = int(parts[5])
                second = int(parts[6])

                self.set_manual_time(year, month, day, hour, minute, second)

                print(
                    "OK,{},TIME,{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                        self.device_id,
                        year,
                        month,
                        day,
                        hour,
                        minute,
                        second,
                    )
                )

            elif cmd == "HEARTBEAT_ON":
                self.heartbeat_enabled = True
                self.last_heartbeat_ms = now_ms()
                print("OK,{},HEARTBEAT_ON".format(self.device_id))

            elif cmd == "HEARTBEAT_OFF":
                self.heartbeat_enabled = False
                print("OK,{},HEARTBEAT_OFF".format(self.device_id))

            else:
                print("ERR,{},unknown command: {}".format(self.device_id, line))

        except Exception as e:
            print("ERR,{},{}".format(self.device_id, e))

    # ==========================================================
    # Main loop
    # ==========================================================

    def run_forever(self):
        print("READY,{}".format(self.device_id))
        print(self.help_text())

        poll = select.poll()
        poll.register(sys.stdin, select.POLLIN)

        self.last_heartbeat_ms = now_ms()

        while True:
            self.loop_counter += 1

            try:
                if poll.poll(50):
                    line = sys.stdin.readline()
                    self.handle_command(line)

                self.schedule_tick()

                if self.heartbeat_enabled:
                    current_ms = now_ms()

                    if diff_ms(current_ms, self.last_heartbeat_ms) >= self.heartbeat_interval_ms:
                        self.last_heartbeat_ms = current_ms
                        print(self.heartbeat_text())

                time.sleep(0.05)

            except Exception as e:
                # Keep running instead of silently dying.
                print("ERR,{},MAIN_LOOP,{}".format(self.device_id, e))
                time.sleep(0.5)