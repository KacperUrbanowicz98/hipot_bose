"""
relay_test.py
-------------
Diagnostyczny skrypt dla ESP8266 przekaźnik Hi-Pot ↔ Ground Bond.
Testuje komunikację i poprawność odpowiedzi przed wdrożeniem do apki.

Użycie:
  python relay_test.py
  python relay_test.py --port COM5
  python relay_test.py --dry-run
"""

import serial
import serial.tools.list_ports
import time
import argparse

PORT     = "COM3"    # ← zmień na port ESP8266 (CH340/CP2102)
BAUDRATE = 115200
TIMEOUT  = 3


# ── Mock Serial ────────────────────────────────────────────────────────────
class MockSerial:
    """Symuluje odpowiedzi ESP8266 bez podłączonego sprzętu."""

    def __init__(self):
        self.is_open   = True
        self._state    = "HIPOT"
        self._last_cmd = ""
        self._ready    = False

    def reset_input_buffer(self):
        pass

    def write(self, data: bytes):
        self._last_cmd = data.decode("ascii", errors="replace").strip()

    def read_until(self, terminator=b'\n', size=512) -> bytes:
        time.sleep(0.05)
        cmd = self._last_cmd

        if not self._ready:
            self._ready = True
            return b"READY\n"

        if cmd == "STATUS?":
            return f"STATE:{self._state}\n".encode()
        elif cmd == "PE":
            self._state = "PE"
            return b"OK:PE\n"
        elif cmd == "HIPOT":
            self._state = "HIPOT"
            return b"OK:HIPOT\n"
        elif cmd == "PING":
            return b"PONG\n"
        else:
            return f"ERR:UNKNOWN:{cmd}\n".encode()

    def close(self):
        self.is_open = False


# ── Helpers ────────────────────────────────────────────────────────────────
def send_cmd(ser, cmd: str, wait: float = 0.5) -> str:
    """Wysyła komendę, czeka na odpowiedź, zwraca string."""
    ser.reset_input_buffer()
    ser.write((cmd.strip() + "\n").encode("ascii"))
    time.sleep(0.1)
    raw    = ser.read_until(b'\n', size=256)
    result = raw.decode("ascii", errors="replace").strip()
    print(f"  {cmd:<20} → {result!r}")
    return result


def divider(title=""):
    print(f"\n{'─'*52}")
    if title:
        print(f"  {title}")
    print(f"{'─'*52}")


# ── Fazy testu ─────────────────────────────────────────────────────────────
def phase_ready(ser):
    divider("FAZA 1: Oczekiwanie na READY po connect")
    # ESP wysyła READY automatycznie po otwarciu portu
    time.sleep(1.5)   # czas na reset ESP po otwarciu portu
    if isinstance(ser, MockSerial):
        raw = ser.read_until(b'\n')
    else:
        raw = ser.read_until(b'\n', size=256)
    msg = raw.decode("ascii", errors="replace").strip()
    print(f"  ESP boot msg → {msg!r}")
    if "READY" in msg:
        print("  ✔ ESP gotowy")
        return True
    print("  ⚠ Brak READY — może stary firmware (bez READY w setup)")
    print("  → Kontynuuję mimo to...")
    return True   # nie blokuj — stary firmware też może działać


def phase_status(ser):
    divider("FAZA 2: Zapytanie o aktualny stan (STATUS?)")
    resp = send_cmd(ser, "STATUS?")
    if resp.startswith("STATE:"):
        state = resp.split(":")[1]
        print(f"  ✔ Aktualny stan: {state}")
        return state
    print(f"  ⚠ Nieoczekiwana odpowiedź: {resp!r}")
    print("  → Może stary firmware — komenda STATUS? może nie istnieć")
    return None


def phase_switch_pe(ser):
    divider("FAZA 3: Przełącz na PE (Ground Bond)")
    resp = send_cmd(ser, "PE")
    if resp == "OK:PE":
        print("  ✔ Przekaźnik przełączony na PE")
    elif "WLACZONY" in resp:
        print("  ✔ Przekaźnik przełączony (stary firmware)")
    else:
        print(f"  ⚠ Nieoczekiwana odpowiedź: {resp!r}")
        return False

    # Weryfikacja przez STATUS?
    time.sleep(0.2)
    resp2 = send_cmd(ser, "STATUS?")
    if "STATE:PE" in resp2:
        print("  ✔ STATUS potwierdza: PE")
    else:
        print(f"  ⚠ STATUS nie potwierdza PE: {resp2!r}")
    return True


def phase_switch_hipot(ser):
    divider("FAZA 4: Przełącz z powrotem na HIPOT")
    resp = send_cmd(ser, "HIPOT")
    if resp == "OK:HIPOT":
        print("  ✔ Przekaźnik przełączony na HIPOT")
    elif "WYLACZONY" in resp:
        print("  ✔ Przekaźnik przełączony (stary firmware)")
    else:
        print(f"  ⚠ Nieoczekiwana odpowiedź: {resp!r}")
        return False

    # Weryfikacja przez STATUS?
    time.sleep(0.2)
    resp2 = send_cmd(ser, "STATUS?")
    if "STATE:HIPOT" in resp2:
        print("  ✔ STATUS potwierdza: HIPOT")
    else:
        print(f"  ⚠ STATUS nie potwierdza HIPOT: {resp2!r}")
    return True


def phase_ping(ser):
    divider("FAZA 5: Ping / Watchdog heartbeat")
    resp = send_cmd(ser, "PING")
    if resp == "PONG":
        print("  ✔ PING → PONG działa")
    else:
        print(f"  ⚠ Brak PONG: {resp!r} — może stary firmware")


def phase_unknown(ser):
    divider("FAZA 6: Test nieznanej komendy (obsługa błędu)")
    resp = send_cmd(ser, "BLABLABLA")
    if resp.startswith("ERR:"):
        print(f"  ✔ Błąd obsłużony poprawnie: {resp!r}")
    elif "Nieznana" in resp:
        print(f"  ✔ Błąd obsłużony (stary firmware): {resp!r}")
    else:
        print(f"  ⚠ Nieoczekiwana odpowiedź: {resp!r}")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Relay diagnostic test — ESP8266")
    parser.add_argument("--port",    default=PORT,
                        help=f"Port COM ESP8266 (domyślnie {PORT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Symulacja bez sprzętu (MockSerial)")
    args = parser.parse_args()

    print("\n" + "═"*52)
    print("  RELAY DIAGNOSTIC — ESP8266 / Slaughter 4320")
    if args.dry_run:
        print("  Tryb: DRY RUN (MockSerial)")
    else:
        print(f"  Tryb: LIVE   Port: {args.port}  Baud: {BAUDRATE}")
    print("═"*52)

    if args.dry_run:
        ser = MockSerial()
    else:
        available = [p.device for p in serial.tools.list_ports.comports()]
        print(f"\nDostępne porty: {available}")
        if args.port not in available:
            print(f"\n✘ Port {args.port} niedostępny — użyj --dry-run lub --port COMXX")
            return
        try:
            ser = serial.Serial(
                port=args.port, baudrate=BAUDRATE,
                timeout=TIMEOUT
            )
            print(f"\n✔ Połączono z {args.port}")
        except Exception as e:
            print(f"\n✘ Błąd połączenia: {e}")
            return

    try:
        phase_ready(ser)
        phase_status(ser)
        phase_switch_pe(ser)
        phase_switch_hipot(ser)
        phase_ping(ser)
        phase_unknown(ser)

        divider("PODSUMOWANIE")
        print("  Jeśli wszystkie fazy ✔ — relay_controller.py gotowy do wdrożenia")
        print("  Jeśli ⚠ na STATUS?/PING — wgraj nowy firmware Arduino przed wdrożeniem")

    except KeyboardInterrupt:
        print("\n\n  Przerwano przez użytkownika.")
    finally:
        ser.close()
        print(f"\n  Połączenie zamknięte.")


if __name__ == "__main__":
    main()