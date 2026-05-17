"""
read_li850_everything.py

Reads everything coming from the LI-850 serial port and prints it.

Install:
    python -m pip install pyserial

Run:
    python read_li850_everything.py
"""

import time
import serial
from serial.tools import list_ports


# =========================
# USER SETTINGS
# =========================

LI850_PORT = "COM11"     # change this to your LI-850 COM port
BAUDRATE = 9600
TIMEOUT_S = 1.0

PRINT_AS_REPR = True    # shows hidden characters like \r\n
PRINT_AS_TEXT = True    # prints readable text
PRINT_AS_HEX = False    # set True if output looks corrupted

READ_SIZE = 1024        # bytes per read


def list_available_ports():
    print("\nAvailable serial ports:")
    for port in list_ports.comports():
        print(f"  {port.device:8s}  {port.description}")
    print()


def main():
    list_available_ports()

    print(f"Opening LI-850 on {LI850_PORT}")
    print(f"Serial settings: {BAUDRATE} baud, 8N1, no flow control")
    print("Press Ctrl+C to stop.\n")

    ser = serial.Serial(
        port=LI850_PORT,
        baudrate=BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=TIMEOUT_S,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )

    total_bytes = 0

    try:
        while True:
            data = ser.read(READ_SIZE)

            if not data:
                print("[no data]")
                time.sleep(0.5)
                continue

            total_bytes += len(data)

            print("\n" + "=" * 80)
            print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Bytes read this chunk: {len(data)}")
            print(f"Total bytes read: {total_bytes}")

            if PRINT_AS_REPR:
                print("\n--- repr(data decoded) ---")
                print(repr(data.decode(errors="replace")))

            if PRINT_AS_TEXT:
                print("\n--- text ---")
                print(data.decode(errors="replace"))

            if PRINT_AS_HEX:
                print("\n--- hex ---")
                print(data.hex(" "))

    except KeyboardInterrupt:
        print("\nStopped by user.")

    finally:
        ser.close()
        print("Serial port closed.")


if __name__ == "__main__":
    main()