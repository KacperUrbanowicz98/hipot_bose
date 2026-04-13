import serial
import time
import threading


class HipotController:
    def __init__(self, port: str, baudrate: int = 9600, timeout: int = 3):
        self.port     = port
        self.baudrate = baudrate
        self.timeout  = timeout
        self._serial  = None
        self._lock    = threading.Lock()

    # ── Połączenie ─────────────────────────────────────────────────────────
    def connect(self):
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            rtscts=False,
            dsrdtr=False
        )
        time.sleep(0.3)

    def disconnect(self):
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Komunikacja ────────────────────────────────────────────────────────
    def _send(self, command: str, wait: float = 0.6) -> bytes:
        """Komenda sterująca — używa read_all(), drukuje debug."""
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write((command.strip() + "\r\n").encode("ascii"))
            time.sleep(wait)
            resp = self._serial.read_all()
            print(f"SEND  >> {command!r:20} | RESP << {resp!r}")
            return resp

    def _query(self, command: str, wait: float = 0.6) -> str:
        """Komenda query — używa read_until(), zwraca string."""
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write((command.strip() + "\r\n").encode("ascii"))
            time.sleep(wait)
            raw = self._serial.read_until(b'\n', size=512)
            print(f"QUERY >> {command!r:20} | RESP << {raw!r}")
            return raw.decode("ascii", errors="replace").strip()

    def _cmd(self, command: str, wait: float = 0.6) -> bool:
        """Wysyła komendę, zwraca True jeśli ACK (0x06) w odpowiedzi."""
        resp = self._send(command, wait)
        return b'\x06' in resp

    # ── Sekwencja testowa ──────────────────────────────────────────────────
    def run_test(self, profile: dict = None, test_timeout: float = 20.0) -> dict:
        """
        Odpala gotowy profil 1 z pamięci urządzenia.
        Aktualny profil na urządzeniu:
            Memory 1 — ACW, 2.5 kV, HI 2.43 mA, LO 1.0 mA, ramp 1s, dwell 3s, 50Hz
        """
        result = {
            "result":  None,
            "voltage": None,
            "current": None,
            "time":    None,
            "status":  "error",
            "error":   None
        }

        try:
            # 1. Reset na start — czysty stan
            self._send("RESET", wait=0.3)

            # 2. Włącz PLC Remote — wymagane do TEST przez RS-232
            if not self._cmd("SPR 1", wait=0.6):
                result["error"] = "SPR 1 — brak ACK (PLC Remote ON)"
                return result

            # 3. Załaduj gotowy profil 1 z pamięci urządzenia
            if not self._cmd("FL 1", wait=0.6):
                result["error"] = "FL 1 — brak ACK"
                return result

            # 4. Start testu
            if not self._cmd("TEST", wait=0.4):
                result["error"] = "TEST — brak ACK"
                return result

            # 5. Czekaj na zakończenie przez *OPC? (polling co 1s)
            deadline = time.time() + test_timeout
            finished = False

            while time.time() < deadline:
                time.sleep(1)
                opc = self._query("*OPC?", wait=0.3)
                if "1" in opc:
                    finished = True
                    break

            if not finished:
                result["status"] = "timeout"
                result["error"]  = "Timeout — brak odpowiedzi *OPC? w czasie"
                return result

            # 6. Sprawdź status na podstawie RD 1? (STB? nie zawsze działa)
            raw = self._query("RD 1?", wait=0.4)
            parts = [p.strip() for p in raw.split(",")]

            print(f"  RD 1? parts ({len(parts)}): {parts}")

            if len(parts) >= 7:
                result["result"] = parts[3]
                result["voltage"] = parts[4]
                result["current"] = parts[5]
                result["time"] = parts[6]

                if parts[3] == "Pass":
                    result["status"] = "pass"
                elif parts[3] == "Fail":
                    result["status"] = "fail"
                else:
                    result["status"] = "done"
            else:
                result["raw_result"] = raw
                result["status"] = "done"
                result["result"] = "Unknown"

            # 7. Odczytaj wynik kroku 1
            raw   = self._query("RD 1?", wait=0.4)
            parts = [p.strip() for p in raw.split(",")]

            print(f"  RD 1? parts ({len(parts)}): {parts}")

            if len(parts) >= 7:
                result["result"] = parts[3]  # Pass / Fail  ← była 2
                result["voltage"] = parts[4]  # 2.50 kV      ← była 3
                result["current"] = parts[5]  # 1.82 mA      ← była 4
                result["time"] = parts[6]  # 3.0 s         ← była 5
            else:
                # Fallback — RD 1? zwróciło niestandardowy format
                result["raw_result"] = raw
                if result["status"] == "pass":
                    result["result"] = "Pass"
                elif result["status"] == "fail":
                    result["result"] = "Fail"
                elif result["status"] == "abort":
                    result["result"] = "Abort"
                else:
                    result["result"] = "Unknown"

            return result

        except Exception as e:
            result["error"] = str(e)
            return result

        finally:
            # Zawsze wyłącz PLC Remote i zresetuj
            try:
                self._send("SPR 0", wait=0.3)
            except Exception:
                pass
            try:
                self._send("RESET", wait=0.3)
            except Exception:
                pass