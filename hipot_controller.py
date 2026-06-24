import re
import serial
import serial.tools.list_ports
import time
import threading


# ── Kody błędów Slaughter 4320 ─────────────────────────────────────────────
FAIL_CODES = {
    "0001": "HI limit przekroczony — prąd za wysoki",
    "0002": "LO limit — prąd za niski (brak kontaktu z DUT?)",
    "0003": "Arc Detection — wykryto wyładowanie łukowe",
    "0004": "Interlock — sprawdź pokrywę / podłączenie DUT",
    "0005": "Timeout testu — DUT nie odpowiedział w czasie",
    "0006": "Ramp Failure — napięcie nie osiągnęło wartości docelowej",
}


class HipotError(Exception):
    pass


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_meter(raw: str) -> tuple[str, str]:
    """Rozdziela wartość od jednostki: '45.2mΩ' → ('45.2', 'mΩ')"""
    m = re.match(r"([0-9.]+)(.*)", raw.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return raw.strip(), ""


class HipotController:
    def __init__(self, port: str, baudrate: int = 9600, timeout: int = 3):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._lock = threading.Lock()

    # ── Połączenie ─────────────────────────────────────────────────────────
    def connect(self):
        available = [p.device for p in serial.tools.list_ports.comports()]
        if self.port not in available:
            raise HipotError(
                f"Port {self.port} niedostępny. "
                f"Dostępne porty: {', '.join(available) if available else 'brak'}"
            )
        try:
            self._serial = serial.Serial(
                port=self.port, baudrate=self.baudrate,
                bytesize=8, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout, rtscts=False, dsrdtr=False
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
        resp = self._send(command, wait)
        if b'\x15' in resp:
            raise HipotError(f"NAK od testera na komendę '{command}' — komenda odrzucona.")
        return b'\x06' in resp

    # ── Parser wyników ─────────────────────────────────────────────────────
    def _read_result_hipot(self, result: dict) -> dict:
        """Odczyt wyniku AC/DC Hipot — RD 1?"""
        raw = self._query("RD 1?", wait=0.5)
        parts = [p.strip() for p in raw.split(",")]
        print(f"RD 1? parts ({len(parts)}): {parts}")

        # NAK = brak wyniku (test bez DUT lub zakończony błędem)
        if not parts or '\x15' in parts[0]:
            result["status"]     = "error"
            result["error_desc"] = "RD 1? zwróciło NAK — brak wyniku (test bez DUT?)"
            return result

        if len(parts) >= 6:
            verdict          = parts[2]
            result["result"] = verdict
            val, unit        = _parse_meter(parts[3])
            result["voltage"] = f"{val} {unit}".strip()
            val, unit         = _parse_meter(parts[4])
            result["current"] = f"{val} {unit}".strip()
            val, unit         = _parse_meter(parts[5])
            result["time"]    = f"{val} {unit}".strip()

            error_code = parts[6].strip() if len(parts) > 6 else ""
            if error_code and error_code != "0000":
                result["error_code"] = error_code
                result["error_desc"] = FAIL_CODES.get(
                    error_code, f"Nieznany kod błędu: {error_code}"
                )

            result["status"] = "pass" if verdict == "Pass" else (
                "fail" if verdict == "Fail" else "done"
            )
            if verdict == "Fail" and not result.get("error_desc"):
                result["error_desc"] = "FAIL — brak szczegółowego kodu błędu"
        else:
            result["raw_result"] = raw
            result["result"]     = "Unknown"
            result["status"]     = "done"
            result["error_desc"] = f"Nieoczekiwana odpowiedź RD 1?: '{raw}'"

        return result

    def _read_result_gnd(self, result: dict) -> dict:
        """Odczyt wyniku Ground Bond — RD 2?
        Format z testera: GND, <resistance>, <current>, <time>, Pass/Fail
        lub: <mem-step>, GND, Pass/Fail, <resistance>, <current>, <time>
        """
        raw = self._query("RD 2?", wait=0.5)
        parts = [p.strip() for p in raw.split(",")]
        print(f"RD 2? parts ({len(parts)}): {parts}")

        # NAK = brak wyniku
        if not parts or '\x15' in parts[0]:
            result["status"]     = "error"
            result["error_desc"] = "RD 2? zwróciło NAK — brak wyniku (test bez DUT?)"
            return result

        if len(parts) >= 5:
            # Wykryj format: czy parts[0] zawiera "-" (np. "1-2") → format z mem-step
            if "-" in parts[0]:
                # Format: mem-step, GND, verdict, resistance, current, time
                verdict = parts[2]
                res_val, res_unit = _parse_meter(parts[3])
                cur_val, cur_unit = _parse_meter(parts[4])
                tim_val, tim_unit = _parse_meter(parts[5]) if len(parts) > 5 else ("", "")
            else:
                # Format: GND, resistance, current, time, verdict
                verdict = parts[4] if len(parts) > 4 else "Unknown"
                res_val, res_unit = _parse_meter(parts[1])
                cur_val, cur_unit = _parse_meter(parts[2])
                tim_val, tim_unit = _parse_meter(parts[3])

            result["result"]     = verdict
            result["resistance"] = f"{res_val} {res_unit}".strip()
            result["current"]    = f"{cur_val} {cur_unit}".strip()
            result["time"]       = f"{tim_val} {tim_unit}".strip()
            result["status"]     = "pass" if verdict == "Pass" else (
                "fail" if verdict == "Fail" else "done"
            )

            if verdict == "Fail":
                res_float = _safe_float(res_val)
                hi_limit  = result.get("_hi_limit")
                if res_float and hi_limit and res_float > hi_limit:
                    result["error_desc"] = (
                        f"Rezystancja {res_val}{res_unit} > HI limit {hi_limit} mΩ"
                    )
                else:
                    result["error_desc"] = "FAIL Ground Bond"
        else:
            result["raw_result"] = raw
            result["result"]     = "Unknown"
            result["status"]     = "done"
            result["error_desc"] = f"Nieoczekiwana odpowiedź RD 2?: '{raw}'"

        return result

    # ── Test AC/DC Hipot ───────────────────────────────────────────────────
    def program_and_run(self, profile: dict, test_timeout: float = 30.0) -> dict:
        result = {
            "result": None, "voltage": None, "current": None,
            "time": None, "status": "error", "error": None,
            "error_code": None, "error_desc": None,
        }
        try:
            voltage   = profile.get("voltage",   3.0)
            hi_limit  = profile.get("hi_limit",  10.0)
            lo_limit  = profile.get("lo_limit",  0.0)
            ramp      = profile.get("ramp",      1.0)
            dwell     = profile.get("dwell",     2.0)
            frequency = profile.get("frequency", 0)

            result["_hi_limit"] = hi_limit
            result["_lo_limit"] = lo_limit

            self._send("RESET", wait=0.4)

            steps = [
                ("SPR 1",              0.6, "Remote ON"),
                ("FL 1",               0.3, "File Load"),
                ("SS 1",               0.3, "Select Step 1"),
                (f"EV {voltage:.2f}",  0.3, f"Napięcie {voltage:.2f} kV"),
                (f"EH {hi_limit:.2f}", 0.3, f"HI limit {hi_limit:.2f} mA"),
                (f"EL {lo_limit:.2f}", 0.3, f"LO limit {lo_limit:.2f} mA"),
                (f"ERU {ramp:.1f}",    0.3, f"Ramp {ramp:.1f} s"),
                (f"EDW {dwell:.1f}",   0.3, f"Dwell {dwell:.1f} s"),
                (f"EF {frequency}",    0.3, f"Freq {'50Hz' if frequency==0 else '60Hz'}"),
            ]
            for cmd, wait, desc in steps:
                if not self._cmd(cmd, wait):
                    result["error"] = f"Brak ACK na '{cmd}' ({desc})."
                    return result

            if not self._cmd("TEST", wait=0.5):
                result["error"] = (
                    "TEST — brak ACK. Sprawdź: Interlock, tryb Remote, podłączenie DUT."
                )
                return result

            wait_time = min(ramp + dwell + 1.5, test_timeout)
            print(f"Czekam {wait_time:.1f}s na zakończenie testu Hipot...")
            time.sleep(wait_time)

            return self._read_result_hipot(result)

        except HipotError as e:
            result["error"] = str(e)
            return result
        except Exception as e:
            result["error"] = f"Nieoczekiwany błąd: {e}"
            return result
        finally:
            try: self._send("SPR 0", wait=0.3)
            except Exception: pass
            try: self._send("RESET", wait=0.3)
            except Exception: pass

    # ── Test Ground Bond ───────────────────────────────────────────────────
    def run_ground_bond(self, profile: dict, test_timeout: float = 15.0) -> dict:
        """
        Sekwencja GND Bond (potwierdzona live na Slaughter 4320):
          RESET → SPR 1 → FL 1 → SS 2 → SAG →
          EC → EH → EL → EDW → EO → EF → TEST → RD 2?
        """
        result = {
            "result": None, "resistance": None, "current": None,
            "time": None, "status": "error", "error": None,
            "error_desc": None,
        }
        try:
            current   = profile.get("current",   10.0)   # A
            hi_limit  = profile.get("hi_limit",  100)    # mΩ
            lo_limit  = profile.get("lo_limit",  0)      # mΩ
            dwell     = profile.get("dwell",     1.0)    # s
            offset    = profile.get("offset",    0)      # mΩ
            frequency = profile.get("frequency", 1)      # 1=60Hz, 0=50Hz

            result["_hi_limit"] = hi_limit

            self._send("RESET", wait=0.4)

            steps = [
                ("SPR 1",              0.6, "Remote ON"),
                ("FL 1",               0.3, "File Load"),
                ("SS 2",               0.3, "Select Step 2"),
                ("SAG",                0.3, "Step Add GND"),
                (f"EC {current:.2f}", 0.3, f"Current {current:.2f} A"),
                (f"EH {hi_limit}",    0.3, f"HI limit {hi_limit} mΩ"),
                (f"EL {lo_limit}",    0.3, f"LO limit {lo_limit} mΩ"),
                (f"EDW {dwell:.1f}",  0.3, f"Dwell {dwell:.1f} s"),
                (f"EO {offset}",      0.3, f"Offset {offset} mΩ"),
                (f"EF {frequency}",   0.3, f"Freq {'60Hz' if frequency==1 else '50Hz'}"),
            ]
            for cmd, wait, desc in steps:
                if not self._cmd(cmd, wait):
                    result["error"] = f"Brak ACK na '{cmd}' ({desc})."
                    return result

            if not self._cmd("TEST", wait=0.5):
                result["error"] = (
                    "TEST — brak ACK. Sprawdź: Interlock, tryb Remote, podłączenie DUT."
                )
                return result

            wait_time = min(dwell + 1.5, test_timeout)
            print(f"Czekam {wait_time:.1f}s na zakończenie testu GND Bond...")
            time.sleep(wait_time)

            return self._read_result_gnd(result)

        except HipotError as e:
            result["error"] = str(e)
            return result
        except Exception as e:
            result["error"] = f"Nieoczekiwany błąd: {e}"
            return result
        finally:
            try: self._send("SPR 0", wait=0.3)
            except Exception: pass
            try: self._send("RESET", wait=0.3)
            except Exception: pass

    # ── Fallback: uruchom profil z pamięci urządzenia ──────────────────────
    def run_test(self, profile: dict = None, test_timeout: float = 20.0) -> dict:
        result = {
            "result": None, "voltage": None, "current": None,
            "time": None, "status": "error", "error": None,
            "error_code": None, "error_desc": None,
        }
        try:
            ramp  = profile.get("ramp",  1.0) if profile else 1.0
            dwell = profile.get("dwell", 2.0) if profile else 2.0

            self._send("RESET", wait=0.3)

            if not self._cmd("SPR 1", wait=0.6):
                result["error"] = "SPR 1 — brak ACK (Remote ON)."
                return result
            if not self._cmd("FL 1", wait=0.6):
                result["error"] = "FL 1 — brak ACK (File Load)."
                return result
            if not self._cmd("TEST", wait=0.4):
                result["error"] = "TEST — brak ACK. Sprawdź Interlock."
                return result

            wait_time = min(ramp + dwell + 1.5, test_timeout)
            print(f"Czekam {wait_time:.1f}s na zakończenie testu (fallback)...")
            time.sleep(wait_time)

            return self._read_result_hipot(result)

        except HipotError as e:
            result["error"] = str(e)
            return result
        except Exception as e:
            result["error"] = f"Nieoczekiwany błąd: {e}"
            return result
        finally:
            try: self._send("SPR 0", wait=0.3)
            except Exception: pass
            try: self._send("RESET", wait=0.3)
            except Exception: pass