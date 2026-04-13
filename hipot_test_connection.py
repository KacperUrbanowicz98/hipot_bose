import serial
import time

# ============================================================
# KONFIGURACJA
# ============================================================
PORT = 'COM10'
BAUD = 9600

ACK = b'\x06\n'
NAK = b'\x15\n'

def status(resp):
    if resp == ACK:   return "✅ ACK"
    elif resp == NAK: return "❌ NAK"
    elif resp == b'': return "⏳ BRAK ODPOWIEDZI"
    else:             return f"📨 RAW: {resp!r}"

def send(ser, cmd, wait=0.5):
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    return ser.read_all()

# ============================================================
# START
# ============================================================
print("=" * 50)
print("  HIPOT RS-232 DIAGNOSTYKA")
print("=" * 50)

ser = serial.Serial(PORT, BAUD, timeout=2)
ser.rts = True
ser.dtr = True
time.sleep(0.3)
print(f"\nPort {PORT} otwarty ✅\n")

# ============================================================
# FAZA 1: Komendy sterujące (Bez napiecia)
# ============================================================
print("--- FAZA 1: Komendy sterujące ---")

komendy_faza1 = [
    (b"RESET\r",  "Reset testera"),
    (b"STOP\r",   "Stop test"),
    (b"LOCAL\r",  "Tryb lokalny"),
    (b"REMOTE\r", "Tryb zdalny"),
]

for cmd, opis in komendy_faza1:
    resp = send(ser, cmd)
    print(f"  {opis:25} {cmd!r:15} → {status(resp)}")

time.sleep(0.5)

# ============================================================
# FAZA 2: Ładowanie profili (bez uruchamiania testu)
# ============================================================
print("\n--- FAZA 2: Ładowanie profili (FL) ---")

for i in range(1, 7):
    cmd = f"FL {i}\r".encode()
    resp = send(ser, cmd, wait=0.3)
    print(f"  Profil {i}:  {cmd!r:15} → {status(resp)}")

# ============================================================
# FAZA 3: Odczyt statusu (tylko odczyt)
# ============================================================
print("\n--- FAZA 3: Odczyt statusu/wyników ---")

komendy_faza3 = [
    (b"STS\r",  "Status"),
    (b"RD\r",   "Read result"),
    (b"SA\r",   "Step/aktualny wynik"),
    (b"EM\r",   "Error message"),
]

for cmd, opis in komendy_faza3:
    resp = send(ser, cmd)
    print(f"  {opis:25} {cmd!r:15} → {status(resp)}")

# ============================================================
# FAZA 4: TEST na profilu 1 (bez DUT)
# ============================================================
print("\n--- FAZA 4: Pełna sekwencja test profil 1 ---")
print("  (bez DUT - tester ruszy test ale nie znajdzie urządzenia)\n")

# Załaduj profil 1
resp = send(ser, b"FL 1\r", wait=0.5)
print(f"  FL 1 (ładuj profil):     → {status(resp)}")

# Uruchom test
resp = send(ser, b"TEST\r", wait=0.3)
print(f"  TEST (start):            → {status(resp)}")

# Czekaj na zakończenie testu (maks 10 sek)
print("  Czekam na wynik testu...", end="", flush=True)
for i in range(10):
    time.sleep(1)
    print(".", end="", flush=True)
    ser.reset_input_buffer()
    ser.write(b"RD\r")
    time.sleep(0.3)
    wynik = ser.read_all()
    if wynik and wynik not in [ACK, NAK]:
        print(f"\n  Wynik po {i+1}s: {wynik!r}")
        break
    elif wynik == ACK:
        print(f"\n  RD → ACK (test w toku lub wynik gotowy)")
        break
else:
    print("\n  Timeout - brak wyniku w 10s")

# Jeszcze raz odczytaj wynik
time.sleep(1)
resp = send(ser, b"RD\r")
print(f"  RD (końcowy wynik):      → {status(resp)}")

# ============================================================
# koniec procedury
# ============================================================
ser.write(b"STOP\r")
time.sleep(0.2)
ser.close()

print("\n" + "=" * 50)
print("  DIAGNOSTYKA ZAKOŃCZONA")
print("=" * 50)