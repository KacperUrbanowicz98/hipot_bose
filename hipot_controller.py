import serial
import serial.tools.list_ports
import time
import threading


# ── Kody błędów Slaughter 4320 z RD 1? ────────────────────────────────────
FAIL_CODES = {
    "0001": "HI limit przekroczony — prąd za wysoki",
    "0002": "LO limit — prąd za niski (brak kontaktu z DUT?)",
    "0003": "Arc Detection — wykryto wyładowanie łukowe",
    "0004": "Interlock — sprawdź pokrywę / podłączenie DUT",
    "0005": "Timeout testu — DUT nie odpowiedział w czasie",
    "0006": "Ramp Failure — napięcie nie osiągnęło wartości docelowej",
}


class HipotError(Exception):
    """Wyjątek dla błędów komunikacji z Hi-Pot."""
    pass


class HipotController:
    def __init__(self, port: str, baudrate: int = 9600, timeout: int = 3):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._lock = threading.Lock()

    # ── Połączenie ─────────────────────────────────────────────────────────
    def connect(self):
        """Otwiera port COM. Rzuca HipotError jeśli port niedostępny."""
        available = [p.device for p in serial.tools.list_ports.comports()]
        if self.port not in available:
            raise HipotError(
                f"Port {self.port} niedostępny. "
                f"Dostępne porty: {', '.join(available) if available else 'brak'}"
            )
        try:
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
        except serial.SerialException as e:
            raise HipotError(f"Błąd otwarcia portu {self.port}: {e}")

    def disconnect(self):
        if self._serial and self._serial.is_open:
            self._serial.close()
            self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Komunikacja ────────────────────────────────────────────────────────
    def _send(self, command: str, wait: float = 0.6) -> bytes:
        """Wysyła komendę i zwraca surową odpowiedź."""
        if not self.is_connected:
            raise HipotError("Brak połączenia z testerem — port zamknięty.")
        try:
            with self._lock:
                self._serial.reset_input_buffer()
                self._serial.write((command.strip() + "\r\n").encode("ascii"))
                time.sleep(wait)
                resp = self._serial.read_all()
                print(f"SEND  >> {command!r:20} | RESP << {resp!r}")
                return resp
        except serial.SerialException as e:
            raise HipotError(f"Utrata połączenia RS-232 podczas '{command}': {e}")

    def _query(self, command: str, wait: float = 0.6) -> str:
        """Wysyła zapytanie i zwraca odpowiedź jako string."""
        if not self.is_connected:
            raise HipotError("Brak połączenia z testerem — port zamknięty.")
        try:
            with self._lock:
                self._serial.reset_input_buffer()
                self._serial.write((command.strip() + "\r\n").encode("ascii"))
                time.sleep(wait)
                raw = self._serial.read_until(b'\n', size=512)
                print(f"QUERY >> {command!r:20} | RESP << {raw!r}")
                return raw.decode("ascii", errors="replace").strip()
        except serial.SerialException as e:
            raise HipotError(f"Utrata połączenia RS-232 podczas '{command}': {e}")

    def _cmd(self, command: str, wait: float = 0.6) -> bool:
        """Wysyła komendę i sprawdza czy dostał ACK (0x06)."""
        resp = self._send(command, wait)
        if b'\x15' in resp:
            # NAK — tester aktywnie odrzucił komendę
            raise HipotError(f"NAK od testera na komendę '{command}' — komenda odrzucona.")
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

            # Kod błędu (jeśli jest — parts[7] lub dalej)
            error_code = parts[7].strip() if len(parts) > 7 else ""
            if error_code and error_code != "0000":
                result["error_code"]  = error_code
                result["error_desc"]  = FAIL_CODES.get(
                    error_code, f"Nieznany kod błędu: {error_code}"
                )

            if verdict == "Pass":
                result["status"] = "pass"
            elif verdict == "Fail":
                result["status"] = "fail"
                # Uzupełnij opis błędu jeśli nie ma z kodu
                if not result.get("error_desc"):
                    current_val = _safe_float(result["current"])
                    hi_limit    = result.get("_hi_limit", None)
                    lo_limit    = result.get("_lo_limit", None)
                    if hi_limit and current_val and current_val > hi_limit:
                        result["error_desc"] = f"HI limit przekroczony ({current_val} mA > {hi_limit} mA)"
                    elif lo_limit and current_val and current_val < lo_limit:
                        result["error_desc"] = f"LO limit — prąd za niski ({current_val} mA < {lo_limit} mA)"
                    else:
                        result["error_desc"] = "FAIL — brak szczegółowego kodu błędu"
            else:
                result["status"] = "done"
        else:
            result["raw_result"] = raw
            result["result"]     = "Unknown"
            result["status"]     = "done"
            result["error_desc"] = f"Nieoczekiwana odpowiedź RD 1?: '{raw}'"

        return result

    # ── Wgraj parametry i uruchom test ─────────────────────────────────────
    def program_and_run(self, profile: dict, test_timeout: float = 30.0) -> dict:
        result = {
            "result":     None,
            "voltage":    None,
            "current":    None,
            "time":       None,
            "status":     "error",
            "error":      None,
            "error_code": None,
            "error_desc": None,
        }

        try:
            voltage   = profile.get("voltage",   3.0)
            hi_limit  = profile.get("hi_limit",  10.0)
            lo_limit  = profile.get("lo_limit",  0.0)
            ramp      = profile.get("ramp",      1.0)
            dwell     = profile.get("dwell",     2.0)
            frequency = profile.get("frequency", 0)

            # Przekaż limity do _read_result (do opisu FAIL)
            result["_hi_limit"] = hi_limit
            result["_lo_limit"] = lo_limit

            # 1. Reset
            self._send("RESET", wait=0.4)

            # 2. Remote ON
            if not self._cmd("SPR 1", wait=0.6):
                result["error"] = "SPR 1 — brak ACK (Remote ON). Sprawdź połączenie RS-232."
                return result

            # 3. Załaduj plik i wybierz krok
            if not self._cmd("FL 1", wait=0.3):
                result["error"] = "FL 1 — brak ACK (File Load). Sprawdź pamięć testera."
                return result

            if not self._cmd("SS 1", wait=0.3):
                result["error"] = "SS 1 — brak ACK (Select Step)."
                return result

            # 4. Wgraj parametry
            params = [
                (f"EV {voltage:.2f}",  f"napięcie {voltage:.2f} kV"),
                (f"EH {hi_limit:.2f}", f"HI limit {hi_limit:.2f} mA"),
                (f"EL {lo_limit:.2f}", f"LO limit {lo_limit:.2f} mA"),
                (f"ERU {ramp:.1f}",    f"ramp {ramp:.1f} s"),
                (f"EDW {dwell:.1f}",   f"dwell {dwell:.1f} s"),
                (f"EF {frequency}",    f"częstotliwość ({'50Hz' if frequency == 1 else '60Hz'})"),
            ]
            for cmd, desc in params:
                if not self._cmd(cmd, wait=0.3):
                    result["error"] = f"Brak ACK na '{cmd}' ({desc}). Parametr nie został zaakceptowany."
                    return result

            # 5. Start testu — brak ACK może oznaczać Interlock
            if not self._cmd("TEST", wait=0.5):
                result["error"] = (
                    "TEST — brak ACK. Możliwe przyczyny: "
                    "Interlock aktywny (sprawdź pokrywę/podłączenie DUT), "
                    "tester w trybie lokalnym, lub błąd komunikacji."
                )
                return result

            # 6. Czekaj: ramp + dwell + bufor, max test_timeout
            wait_time = min(ramp + dwell + 1.5, test_timeout)
            print(f"Czekam {wait_time:.1f}s na zakończenie testu...")
            time.sleep(wait_time)

            # 7. Pobierz wynik
            return self._read_result(result)

        except HipotError as e:
            result["error"] = str(e)
            return result
        except Exception as e:
            result["error"] = f"Nieoczekiwany błąd aplikacji: {e}"
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
        """Odpala gotowy profil 1 z pamięci urządzenia (FL 1). Fallback."""
        result = {
            "result":     None,
            "voltage":    None,
            "current":    None,
            "time":       None,
            "status":     "error",
            "error":      None,
            "error_code": None,
            "error_desc": None,
        }

        try:
            ramp  = profile.get("ramp",  1.0) if profile else 1.0
            dwell = profile.get("dwell", 2.0) if profile else 2.0

            self._send("RESET", wait=0.3)

            if not self._cmd("SPR 1", wait=0.6):
                result["error"] = "SPR 1 — brak ACK (Remote ON). Sprawdź połączenie RS-232."
                return result

            if not self._cmd("FL 1", wait=0.6):
                result["error"] = "FL 1 — brak ACK (File Load)."
                return result

            if not self._cmd("TEST", wait=0.4):
                result["error"] = (
                    "TEST — brak ACK. Możliwe przyczyny: "
                    "Interlock aktywny (sprawdź pokrywę/podłączenie DUT), "
                    "tester w trybie lokalnym."
                )
                return result

            wait_time = min(ramp + dwell + 1.5, test_timeout)
            print(f"Czekam {wait_time:.1f}s na zakończenie testu...")
            time.sleep(wait_time)

            return self._read_result(result)

        except HipotError as e:
            result["error"] = str(e)
            return result
        except Exception as e:
            result["error"] = f"Nieoczekiwany błąd aplikacji: {e}"
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


# ── Pomocnicza ─────────────────────────────────────────────────────────────
def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None