import serial
import time

ser = serial.Serial('COM9', 9600, timeout=0.5)
ser.rts = True
time.sleep(0.2)

ACK = b'\x06\n'
NAK = b'\x15\n'

def wyslij(cmd, wait=0.3):
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    return ser.read_all()

# 1. Start testu
resp = wyslij(b"TEST\r")
print(f"TEST: {resp!r}")

# 2. Polluj co 1 sekundę przez 30 sekund
print("Pollowanie statusu po teście...")
komendy_odczytu = [b"RD\r", b"RS\r", b"SS\r", b"STS\r", b"MEAS?\r", b"RES\r"]

for i in range(30):
    time.sleep(1.0)
    print(f"\n--- Sekunda {i+1} ---")
    for cmd in komendy_odczytu:
        r = wyslij(cmd, wait=0.2)
        if r and r != NAK:
            print(f"  {cmd!r} → {r!r}  ← ODPOWIEDŹ!")
        else:
            print(f"  {cmd!r} → NAK/brak")

ser.write(b"STOP\r")
ser.close()