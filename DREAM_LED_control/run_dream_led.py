# run_dream_led.py
# Optional command-line runner for DREAM LED system.
#
# Normal GUI use:
#   Start_DREAM_LED_Control_Panel.bat
#
# Command-line use:
#   py run_dream_led.py
#
# This file is optional if you only use the GUI.
# All reusable functions are in DREAM_LED_PC.py.

from DREAM_LED_PC import run_dream_led_system


# ==========================================================
# DEVICE LIST
# ==========================================================

EXPECTED_DEVICES = [
    "WHITE_LED_RP2040_1",
    "WHITE_LED_RP2040_2",
    "RED_LED_RP2040",
    "UVIR_LED_RP2040_1",
    "UVIR_LED_RP2040_2",
]


# ==========================================================
# MANUAL COM PORTS
# ==========================================================

MANUAL_PORTS = {
    "WHITE_LED_RP2040_1": "COM7",
    "WHITE_LED_RP2040_2": "COM8",
    "RED_LED_RP2040": "COM16",

    # Add later when connected:
    # "UVIR_LED_RP2040_1": "COMxx",
    # "UVIR_LED_RP2040_2": "COMxx",
}


# ==========================================================
# FIXED LIGHT SCHEDULE
# ==========================================================

LIGHT_ON_TIME = "08:00"
LIGHT_OFF_TIME = "20:00"


# ==========================================================
# OPERATION MODE
# ==========================================================

KEEP_MONITORING = False
STATUS_INTERVAL_S = 60
TURN_OFF_ON_CTRL_C = False

HEARTBEAT_AFTER_CONFIG = "ON"
SCAN_I2C_AFTER_CONNECT = True


# ==========================================================
# SERIAL SETTINGS
# ==========================================================

BAUDRATE = 115200
CONNECT_DELAY_S = 2.0
COMMAND_DELAY_S = 0.20
READ_EXTRA_S = 0.20


# ==========================================================
# FIXED LIGHT SETTINGS
#
# Structure:
# DEVICE_ID -> DAC address -> channel -> output percentage
# ==========================================================

LED_SETTINGS = {
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


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    run_dream_led_system(
        expected_devices=EXPECTED_DEVICES,
        manual_ports=MANUAL_PORTS,
        led_settings=LED_SETTINGS,
        light_on_time=LIGHT_ON_TIME,
        light_off_time=LIGHT_OFF_TIME,
        baudrate=BAUDRATE,
        connect_delay_s=CONNECT_DELAY_S,
        command_delay_s=COMMAND_DELAY_S,
        read_extra_s=READ_EXTRA_S,
        use_manual_ports=True,
        keep_monitoring=KEEP_MONITORING,
        status_interval_s=STATUS_INTERVAL_S,
        turn_off_on_ctrl_c=TURN_OFF_ON_CTRL_C,
        heartbeat_after_config=HEARTBEAT_AFTER_CONFIG,
        scan_i2c_after_connect=SCAN_I2C_AFTER_CONNECT,
    )
