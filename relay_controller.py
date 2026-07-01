"""
relay_controller.py
-------------------
Klasa sterująca przekaźnikiem ESP8266 (HiPot ↔ PE/Ground Bond).

Komendy ESP:
  PE           → przekaźnik na PE (Ground Bond)   → OK:PE
  HIPOT        → przekaźnik na HiPot              → OK:HIPOT
  STATUS?      → zapytanie o stan                 → STATE:PE / STATE:HIPOT
  PING         → heartbeat / reset watchdog       → PONG
  WATCHDOG:OFF → wyłącz watchdog                  → OK:WATCHDOG_OFF
"""

import serial
import serial.tools.list_ports
import time
import threading


class RelayError(Exception):
    """Wyjątek dla błędów komunikacji z przekaźnikiem ESP."""
    pass


class RelayController:
    def __init__(self, port: str, baudrate: int = 115200, timeout: int = 3):
        self.port     = port
        self.baudrate = baudrate
        self.timeout  = timeout
        self._serial  = None
        self._lock    = threading.Lock()

    # ── Połączenie ─────────────────────────────────────────────────────────
    def connect(self):
        """Otwiera port COM bez resetowania ESP (DTR/RTS wyłączone)."""
        available = [p.device for p in serial.tools.list_ports.comports()]
        if self.port not in available:
            raise RelayError(
                f"Port ESP {self.port} niedostępny. "
                f"Dostępne porty: {', '.join(available) if available else 'brak'}"
            )
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                dsrdtr=False,
                rtscts=False,
            )
            # Wyłącz DTR/RTS po otwarciu — ESP nie zresetuje się
            self._serial.dtr = False
            self._serial.rts = False
            time.sleep(0.1)
            self._serial.reset_input_buffer()
            self._wait_for_ready(timeout=1.0)
        except serial.SerialException as e:
            raise RelayError(f"Błąd otwarcia portu ESP {self.port}: {e}")

    def _wait_for_ready(self, timeout: float = 1.0):
        """Czeka na komunikat READY z ESP. Nie blokuje jeśli nie przyjdzie."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            line    = self._serial.read_until(b'\n', size=64)
            decoded = line.decode("ascii", errors="replace").strip()
            if decoded:
                print(f"ESP boot: {decoded!r}")
            if "READY" in decoded:
                return
        print("RelayController: brak READY w boot — kontynuuję")

    def disconnect(self):
        if self._serial and self._serial.is_open:
            self._serial.close()
            self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Komunikacja ────────────────────────────────────────────────────────
    def _send_cmd(self, cmd: str, wait: float = 0.3) -> str:
        """Wysyła komendę do ESP, zwraca odpowiedź jako string."""
        if not self.is_connected:
            raise RelayError("Brak połączenia z ESP — port zamknięty.")
        try:
            with self._lock:
                self._serial.reset_input_buffer()
                self._serial.write((cmd.strip() + "\n").encode("ascii"))
                time.sleep(wait)
                raw  = self._serial.read_until(b'\n', size=256)
                resp = raw.decode("ascii", errors="replace").strip()
                print(f"RELAY >> {cmd!r:15} | RESP << {resp!r}")
                return resp
        except serial.SerialException as e:
            raise RelayError(f"Utrata połączenia z ESP podczas '{cmd}': {e}")

    # ── API publiczne ──────────────────────────────────────────────────────
    def set_pe(self) -> bool:
        """
        Przełącza przekaźnik na PE (Ground Bond).
        Rzuca RelayError jeśli ESP zgłosi RELAY_FAIL.
        """
        resp = self._send_cmd("PE", wait=0.4)
        if resp == "OK:PE":
            return True
        if resp.startswith("ERR:RELAY_FAIL"):
            raise RelayError(
                "Przekaźnik nie przełączył się na PE — "
                "sprawdź okablowanie CHECK_PIN (D3)"
            )
        raise RelayError(f"Nieoczekiwana odpowiedź ESP na PE: {resp!r}")

    def set_hipot(self) -> bool:
        """
        Przełącza przekaźnik na HIPOT (pozycja domyślna/bezpieczna).
        Rzuca RelayError jeśli ESP zgłosi RELAY_FAIL.
        """
        resp = self._send_cmd("HIPOT", wait=0.4)
        if resp == "OK:HIPOT":
            return True
        if resp.startswith("ERR:RELAY_FAIL"):
            raise RelayError(
                "Przekaźnik nie przełączył się na HIPOT — "
                "sprawdź okablowanie CHECK_PIN (D3)"
            )
        raise RelayError(f"Nieoczekiwana odpowiedź ESP na HIPOT: {resp!r}")

    def get_status(self) -> str:
        """Zwraca aktualny stan relay: 'PE', 'HIPOT' lub 'UNKNOWN'."""
        try:
            resp = self._send_cmd("STATUS?", wait=0.3)
            if resp.startswith("STATE:"):
                return resp.split(":")[1].strip()
            return "UNKNOWN"
        except RelayError:
            return "UNKNOWN"

    def ping(self) -> bool:
        """Wysyła PING (reset watchdog ESP). Zwraca True jeśli dostał PONG."""
        try:
            resp = self._send_cmd("PING", wait=0.2)
            return resp == "PONG"
        except RelayError:
            return False

    def watchdog_off(self):
        """Wyłącza watchdog ESP (np. podczas długich testów manualnych)."""
        self._send_cmd("WATCHDOG:OFF", wait=0.3)

    def safe_return_to_hipot(self):
        """
        Awaryjny powrót do HIPOT — używaj w bloku finally.
        Nie rzuca wyjątku — tylko loguje.
        """
        try:
            if self.is_connected:
                self.set_hipot()
        except RelayError as e:
            print(f"[RelayController] safe_return_to_hipot WARN: {e}")
        except Exception as e:
            print(f"[RelayController] safe_return_to_hipot ERROR: {e}")