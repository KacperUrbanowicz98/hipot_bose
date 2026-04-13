import serial
import time

# ============================================================
# KONFIGURACJA
# ============================================================
PORT = 'COM11'
BAUD = 9600

ACK = b'\x06'
NAK = b'\x15'

def status(resp):
    if not resp:
        return "⏳ BRAK ODPOWIEDZI (timeout)"
    if ACK in resp:
        return f"✅ ACK  (raw: {resp!r})"
    if NAK in resp:
        return f"❌ NAK  (raw: {resp!r})"
    return f"📨 RAW: {resp!r}"

def send(ser, cmd, wait=0.6):
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    return ser.read_all()

def query(ser, cmd, wait=0.6):
    """Dla komend query które zwracają dane tekstowe."""
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    return ser.read_until(b'\n', size=512)

# ============================================================
# START
# ============================================================
print("=" * 55)
print("   HIPOT 6330 — RS-232 DIAGNOSTYKA v2")
print("=" * 55)

try:
    ser = serial.Serial(
        port=PORT,
        baudrate=BAUD,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=2,
        rtscts=False,
        dsrdtr=False
    )
    time.sleep(0.3)
    print(f"\nPort {PORT} otwarty ✅")
    print(f"Ustawienia: {BAUD} baud | 8N1 | bez flow control\n")
except serial.SerialException as e:
    print(f"\n❌ Nie można otworzyć portu {PORT}: {e}")
    exit(1)

# ============================================================
# FAZA 1: Identyfikacja urządzenia
# ============================================================
print("--- FAZA 1: Identyfikacja ---")
resp = query(ser, b"*IDN?\r\n")
print(f"  *IDN? (identyfikacja):    → {status(resp)}")

# ============================================================
# FAZA 2: Komendy sterujące (prawidłowe)
# ============================================================
print("\n--- FAZA 2: Komendy sterujące ---")

resp = send(ser, b"RESET\r\n")
print(f"  RESET (stop/abort):       → {status(resp)}")

resp = send(ser, b"SPR 1\r\n")   # PLC Remote ON
print(f"  SPR 1 (Remote ON):        → {status(resp)}")

resp = send(ser, b"SPR 0\r\n")   # PLC Remote OFF
print(f"  SPR 0 (Remote OFF):       → {status(resp)}")

# ============================================================
# FAZA 3: Ładowanie profili
# ============================================================
print("\n--- FAZA 3: Ładowanie profili (FL 1-6) ---")
for i in range(1, 7):
    cmd = f"FL {i}\r\n".encode()
    resp = send(ser, cmd, wait=0.4)
    print(f"  Profil {i}:  {cmd!r:20} → {status(resp)}")

# ============================================================
# FAZA 4: Query — odczyt parametrów
# ============================================================
print("\n--- FAZA 4: Query — odczyt danych ---")

resp = query(ser, b"*STB?\r\n")
print(f"  *STB? (status byte):      → {status(resp)}")

resp = query(ser, b"*ESR?\r\n")
print(f"  *ESR? (event status):     → {status(resp)}")

resp = query(ser, b"LS?\r\n")
print(f"  LS? (parametry kroku):    → {status(resp)}")

resp = query(ser, b"TD?\r\n")
print(f"  TD? (dane testu):         → {status(resp)}")

# ============================================================
# FAZA 5: Pełna sekwencja testowa — profil 1
# ============================================================
print("\n--- FAZA 5: Sekwencja testowa profil 1 (bez DUT) ---")

resp = send(ser, b"FL 1\r\n", wait=0.6)
print(f"  FL 1 (załaduj profil 1):  → {status(resp)}")

resp = send(ser, b"TEST\r\n", wait=0.4)
print(f"  TEST (start):             → {status(resp)}")

# Sprawdzaj *OPC? — zwraca 1 gdy test zakończony
print("\n  Czekam na zakończenie testu (*OPC?)...", end="", flush=True)
wynik_gotowy = False
for i in range(15):
    time.sleep(1)
    print(".", end="", flush=True)
    opc = query(ser, b"*OPC?\r\n", wait=0.3)
    if b'1' in opc:
        print(f"\n  ✅ Test zakończony po {i+1}s")
        wynik_gotowy = True
        break

if not wynik_gotowy:
    print("\n  ⏳ Timeout")

# Odczytaj wynik kroku 1
time.sleep(0.3)
resp = query(ser, b"RD 1?\r\n")
print(f"\n  RD 1? (wynik krok 1):     → {status(resp)}")

# Odczytaj dane TD?
resp = query(ser, b"TD?\r\n")
print(f"  TD? (ostatnie dane):      → {status(resp)}")

# ============================================================
# KONIEC
# ============================================================
ser.write(b"RESET\r\n")
time.sleep(0.2)
ser.close()

print("\n" + "=" * 55)
print("   DIAGNOSTYKA ZAKOŃCZONA")
print("=" * 55)