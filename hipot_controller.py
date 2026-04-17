import serial
import time
import threading


class HipotController:
    def __init__(self, port: str, baudrate: int = 9600, timeout: int = 3):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._lock = threading.Lock()

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
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write((command.strip() + "\r\n").encode("ascii"))
            time.sleep(wait)
            resp = self._serial.read_all()
            print(f"SEND  >> {command!r:20} | RESP << {resp!r}")
            return resp

    def _query(self, command: str, wait: float = 0.6) -> str:
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write((command.strip() + "\r\n").encode("ascii"))
            time.sleep(wait)
            raw = self._serial.read_until(b'\n', size=512)
            print(f"QUERY >> {command!r:20} | RESP << {raw!r}")
            return raw.decode("ascii", errors="replace").strip()

    def _cmd(self, command: str, wait: float = 0.6) -> bool:
        resp = self._send(command, wait)
        return b'\x06' in resp

    def _read_result(self, result: dict) -> dict:
        """Pobiera wynik z RD 1? i uzupełnia słownik result."""
        raw = self._query("RD 1?", wait=0.5)
        parts = [p.strip() for p in raw.split(",")]
        print(f"RD 1? parts ({len(parts)}): {parts}")

        if len(parts) >= 7:
            verdict           = parts[3]
            result["result"]  = verdict
            result["voltage"] = parts[4]
            result["current"] = parts[5]
            result["time"]    = parts[6]
            if verdict == "Pass":
                result["status"] = "pass"
            elif verdict == "Fail":
                result["status"] = "fail"
            else:
                result["status"] = "done"
        else:
            result["raw_result"] = raw
            result["result"]     = "Unknown"
            result["status"]     = "done"

        return result

    # ── Wgraj parametry i uruchom test ─────────────────────────────────────
    def program_and_run(self, profile: dict, test_timeout: float = 30.0) -> dict:
        result = {
            "result": None,
            "voltage": None,
            "current": None,
            "time": None,
            "status": "error",
            "error": None
        }

        try:
            voltage   = profile.get("voltage",   3.0)   # kV
            hi_limit  = profile.get("hi_limit",  10.0)  # mA
            lo_limit  = profile.get("lo_limit",  0.0)   # mA
            ramp      = profile.get("ramp",      1.0)   # s
            dwell     = profile.get("dwell",     2.0)   # s
            frequency = profile.get("frequency", 0)     # 0=60Hz, 1=50Hz

            # 1. Reset
            self._send("RESET", wait=0.4)

            # 2. Remote ON
            if not self._cmd("SPR 1", wait=0.6):
                result["error"] = "SPR 1 — brak ACK (PLC Remote ON)"
                return result

            # 3. Załaduj plik 1 i wybierz krok 1
            if not self._cmd("FL 1", wait=0.3):
                result["error"] = "FL 1 — brak ACK (File Load)"
                return result

            if not self._cmd("SS 1", wait=0.3):
                result["error"] = "SS 1 — brak ACK (Select Step)"
                return result

            # 4. Wgraj parametry
            if not self._cmd(f"EV {voltage:.2f}", wait=0.3):
                result["error"] = f"EV {voltage:.2f} — brak ACK (napięcie)"
                return result

            if not self._cmd(f"EH {hi_limit:.2f}", wait=0.3):
                result["error"] = f"EH {hi_limit:.2f} — brak ACK (HI limit)"
                return result

            if not self._cmd(f"EL {lo_limit:.2f}", wait=0.3):
                result["error"] = f"EL {lo_limit:.2f} — brak ACK (LO limit)"
                return result

            if not self._cmd(f"ERU {ramp:.1f}", wait=0.3):
                result["error"] = f"ERU {ramp:.1f} — brak ACK (ramp)"
                return result

            if not self._cmd(f"EDW {dwell:.1f}", wait=0.3):
                result["error"] = f"EDW {dwell:.1f} — brak ACK (dwell)"
                return result

            if not self._cmd(f"EF {frequency}", wait=0.3):
                result["error"] = f"EF {frequency} — brak ACK (częstotliwość)"
                return result

            # 5. Start testu
            if not self._cmd("TEST", wait=0.5):
                result["error"] = "TEST — brak ACK"
                return result

            # 6. Czekaj stały czas: ramp + dwell + 1.5s bufora
            wait_time = ramp + dwell + 1.5
            print(f"Czekam {wait_time:.1f}s na zakończenie testu...")
            time.sleep(wait_time)

            # 7. Pobierz wynik
            return self._read_result(result)

        except Exception as e:
            result["error"] = str(e)
            return result

        finally:
            try:
                self._send("SPR 0", wait=0.3)
            except Exception:
                pass
            try:
                self._send("RESET", wait=0.3)
            except Exception:
                pass

    # ── Fallback: uruchom profil z pamięci urządzenia ──────────────────────
    def run_test(self, profile: dict = None, test_timeout: float = 20.0) -> dict:
        """
        Odpala gotowy profil 1 z pamięci urządzenia (FL 1).
        Fallback gdy program_and_run nie działa.
        """
        result = {
            "result": None,
            "voltage": None,
            "current": None,
            "time": None,
            "status": "error",
            "error": None
        }

        try:
            ramp  = 1.0
            dwell = 2.0
            if profile:
                ramp  = profile.get("ramp",  ramp)
                dwell = profile.get("dwell", dwell)

            self._send("RESET", wait=0.3)

            if not self._cmd("SPR 1", wait=0.6):
                result["error"] = "SPR 1 — brak ACK (PLC Remote ON)"
                return result

            if not self._cmd("FL 1", wait=0.6):
                result["error"] = "FL 1 — brak ACK"
                return result

            if not self._cmd("TEST", wait=0.4):
                result["error"] = "TEST — brak ACK"
                return result

            # Czekaj stały czas: ramp + dwell + 1.5s bufora
            wait_time = ramp + dwell + 1.5
            print(f"Czekam {wait_time:.1f}s na zakończenie testu...")
            time.sleep(wait_time)

            return self._read_result(result)

        except Exception as e:
            result["error"] = str(e)
            return result

        finally:
            try:
                self._send("SPR 0", wait=0.3)
            except Exception:
                pass
            try:
                self._send("RESET", wait=0.3)
            except Exception:
                pass