import serial
import time

PORT = "COM16"

ser = serial.Serial(PORT, 9600, timeout=2, write_timeout=2)

time.sleep(2)

ser.reset_input_buffer()
ser.reset_output_buffer()

print("Sending ID...")
ser.write(b"ID\n")

time.sleep(1)

print("Bytes waiting:", ser.in_waiting)

while ser.in_waiting:
    print(ser.readline().decode(errors="ignore").strip())

ser.close()