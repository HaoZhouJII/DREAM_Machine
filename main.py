# main.py
# DREAM LED RP2040 entry file

from DREAM_LED_RP2040 import DreamLEDController

DEVICE_ID = "WHITE_LED_RP2040_2"

controller = DreamLEDController(
    device_id=DEVICE_ID,
    i2c_id=0,
    sda_pin=20,
    scl_pin=21,
    pulse_pin=2,
    dac_addrs=(0x60, 0x61, 0x62),
)

controller.run_forever()