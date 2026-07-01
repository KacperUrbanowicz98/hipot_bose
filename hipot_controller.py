import serial
import serial.tools.list_ports
import time
import threading
from relay_controller import RelayController, RelayError

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


class HipotController:
    def __init__(self, port: str, baudrate: int = 9600, timeout: int = 3,
                 relay_port: str = None):
        self.port        = port
        self.baudrate    = baudrate
        self.timeout     = timeout
        self._serial     = None
        self._lock       = threading.Lock()
        self._relay_port = relay_port

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
        if not self.is_connected:
            raise HipotError("Brak połączenia z testerem — port zamknięty.")
        try:
            with self._lock:
                self._serial.reset_input_buffer()
                self._serial.write((command.strip() + "\r\n").encode("ascii"))
                time.sleep(wait)
                resp = self._serial.read_all()
                print(f"SEND >> {command!r:20} | RESP << {resp!r}")
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

    def _query_raw(self, command: str, wait: float = 1.0) -> tuple[bytes, str]:
        if not self.is_connected:
            raise HipotError("Brak połączenia z testerem — port zamknięty.")
        try:
            with self._lock:
                self._serial.reset_input_buffer()
                self._serial.write((command.strip() + "\r\n").encode("ascii"))
                time.sleep(wait)
                raw_bytes = self._serial.read_all()
                raw_str   = raw_bytes.decode("ascii", errors="replace").strip()
                print(f"QUERY_RAW >> {command!r:20} | RESP << {raw_bytes!r}")
                return raw_bytes, raw_str
        except serial.SerialException as e:
            raise HipotError(f"Utrata połączenia RS-232 podczas '{command}': {e}")

    def _cmd(self, command: str, wait: float = 0.6) -> bool:
        resp = self._send(command, wait)
        if b'\x15' in resp:
            raise HipotError(
                f"NAK od testera na komendę '{command}' — komenda odrzucona."
            )
        return b'\x06' in resp

    # ── Odczyt wyniku HiPot (krok 1) ──────────────────────────────────────
    def _read_hipot_result(self, result: dict) -> dict:
        raw_bytes, raw = self._query_raw("RD 1?", wait=1.0)
        parts = [p.strip() for p in raw.split(",")]
        print(f"RD 1? parts ({len(parts)}): {parts}")

        if b'\x15' in raw_bytes and len(raw_bytes) <= 3:
            result["raw_result"] = raw
            result["result"]     = "Unknown"
            result["status"]     = "done"
            result["error_desc"] = "NAK na RD 1? — brak wyniku"
            return result

        # Format: <file>,<step>,<type>,<verdict>,<voltage>,<current>,<time>
        if len(parts) >= 7:
            verdict           = parts[3]
            result["result"]  = verdict
            result["voltage"] = parts[4]
            result["current"] = parts[5]
            result["time"]    = parts[6]

            error_code = parts[7].strip() if len(parts) > 7 else ""
            if error_code and error_code != "0000":
                result["error_code"] = error_code
                result["error_desc"] = FAIL_CODES.get(
                    error_code, f"Nieznany kod błędu: {error_code}"
                )

            if verdict == "Pass":
                result["status"] = "pass"
            elif verdict == "Fail":
                result["status"] = "fail"
                if not result.get("error_desc"):
                    current_val = _safe_float(result["current"])
                    hi_limit    = result.get("_hi_limit")
                    lo_limit    = result.get("_lo_limit")
                    if hi_limit and current_val and current_val > hi_limit:
                        result["error_desc"] = (
                            f"HI limit przekroczony ({current_val} mA > {hi_limit} mA)"
                        )
                    elif (lo_limit is not None and current_val is not None
                          and current_val < lo_limit):
                        result["error_desc"] = (
                            f"LO limit — prąd za niski ({current_val} mA < {lo_limit} mA)"
                        )
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

    # ── Odczyt wyniku Ground Bond ──────────────────────────────────────────
    def _read_gnd_result(self, gnd_result: dict) -> dict:
        # Slaughter 4320: wynik GND trafia pod RD 1? (nie RD 2?) gdy TEST
        # był wysłany osobno — szukamy w obu slotach
        for slot, wait in [("RD 2?", 2.0), ("RD 1?", 1.0)]:
            raw_bytes, raw = self._query_raw(slot, wait=wait)
            parts = [p.strip() for p in raw.split(",")]
            print(f"{slot} parts ({len(parts)}): {parts}")

            if b'\x15' in raw_bytes and len(raw_bytes) <= 3:
                print(f"{slot} → NAK, próbuję następny slot...")
                continue

            # Format A: <file>,<step>,GND,<verdict>,<resistance>,<current>,<time>
            # Format B: GND,<step>,<verdict>,<resistance>,<current>,<time>  (alternatywny)
            if len(parts) >= 6 and (parts[0] == "GND" or parts[2] == "GND"):
                print(f"✔ Wynik GND znaleziony w {slot}")

                if parts[2] == "GND":
                    # Format A (zaobserwowany: 1,2,GND,Pass,25.1,68,1.0)
                    verdict                  = parts[3]
                    gnd_result["resistance"] = parts[4]
                    gnd_result["current"]    = parts[5]
                    gnd_result["time"]       = parts[6] if len(parts) > 6 else "—"
                else:
                    # Format B (GND,<step>,<verdict>,...)
                    verdict                  = parts[2]
                    gnd_result["resistance"] = parts[3]
                    gnd_result["current"]    = parts[4]
                    gnd_result["time"]       = parts[5]

                gnd_result["result"] = verdict
                gnd_result["status"] = "pass" if verdict == "Pass" else "fail"

                if verdict != "Pass":
                    try:
                        res_val  = float(gnd_result["resistance"])
                        hi_limit = gnd_result.get("_hi_limit", 100)
                        if res_val > hi_limit:
                            gnd_result["error_desc"] = (
                                f"Rezystancja {res_val} mΩ > HI limit {hi_limit} mΩ"
                            )
                    except ValueError:
                        pass
                    if not gnd_result.get("error_desc"):
                        gnd_result["error_desc"] = (
                            "FAIL Ground Bond — sprawdź podłączenie PE"
                        )
                return gnd_result

            # Wynik HiPot w tym slocie — zły slot, szukaj dalej
            if len(parts) >= 4 and parts[2] in ("ACW", "DCW", "IR"):
                print(f"{slot} → wynik HiPot ({parts[2]}), nie GND — próbuję następny...")
                continue

            # Nieoczekiwany format
            gnd_result["raw_result"] = raw
            gnd_result["result"]     = "Unknown"
            gnd_result["status"]     = "done"
            gnd_result["error_desc"] = f"Nieoczekiwana odpowiedź {slot}: '{raw}'"
            return gnd_result

        # Oba sloty NAK
        gnd_result["result"]     = "Unknown"
        gnd_result["status"]     = "done"
        gnd_result["error_desc"] = "NAK na RD 2? i RD 1? — brak wyniku Ground Bond"
        return gnd_result

    # ── GŁÓWNA METODA: pełna sekwencja HiPot [+ Ground Bond] ──────────────
    def run_full_sequence(self, profile: dict, test_timeout: float = 30.0) -> dict:
        """
        Sekwencja:
          1. HiPot AC (zawsze, krok 1)
          2. Ground Bond (tylko gdy profile["ground_bond"] != None)
             — relay PE przed testem, relay HIPOT po teście

        HiPot FAIL → Ground Bond NIE jest uruchamiany.
        """
        gnd_profile = profile.get("ground_bond")
        has_gnd     = gnd_profile is not None

        hipot_result = _empty_result()
        gnd_result   = _empty_result() if has_gnd else None

        relay = None
        if has_gnd and self._relay_port:
            relay = RelayController(port=self._relay_port)
            try:
                relay.connect()
                status = relay.get_status()
                print(f"✔ Relay połączony. Stan: {status}")
                if status != "HIPOT":
                    relay.set_hipot()
                    print("✔ Relay → HIPOT (korekcja przed startem)")
            except RelayError as e:
                hipot_result["error"] = f"Nie można połączyć z relay ESP: {e}"
                return {"hipot": hipot_result, "gnd": gnd_result}

        try:
            voltage   = profile.get("voltage",   3.0)
            hi_limit  = profile.get("hi_limit",  10.0)
            lo_limit  = profile.get("lo_limit",  0.0)
            ramp      = profile.get("ramp",      1.0)
            dwell     = profile.get("dwell",     2.0)
            frequency = profile.get("frequency", 0)

            hipot_result["_hi_limit"] = hi_limit
            hipot_result["_lo_limit"] = lo_limit

            self._send("RESET", wait=0.4)

            if not self._cmd("SPR 1", wait=0.6):
                hipot_result["error"] = "SPR 1 — brak ACK (Remote ON)."
                return {"hipot": hipot_result, "gnd": gnd_result}

            if not self._cmd("FL 1", wait=0.3):
                hipot_result["error"] = "FL 1 — brak ACK (File Load)."
                return {"hipot": hipot_result, "gnd": gnd_result}

            # ════════════════════════════════════════════════════
            # KROK 1: HiPot AC
            # ════════════════════════════════════════════════════
            print("\n─── KROK 1: HiPot AC ───────────────────────────────────")

            if not self._cmd("SS 1", wait=0.3):
                hipot_result["error"] = "SS 1 — brak ACK."
                return {"hipot": hipot_result, "gnd": gnd_result}

            for cmd, desc in [
                (f"EV {voltage:.2f}",  f"napięcie {voltage:.2f} kV"),
                (f"EH {hi_limit:.2f}", f"HI limit {hi_limit:.2f} mA"),
                (f"EL {lo_limit:.2f}", f"LO limit {lo_limit:.2f} mA"),
                (f"ERU {ramp:.1f}",    f"ramp {ramp:.1f} s"),
                (f"EDW {dwell:.1f}",   f"dwell {dwell:.1f} s"),
                (f"EF {frequency}",    f"częstotliwość"),
            ]:
                if not self._cmd(cmd, wait=0.3):
                    hipot_result["error"] = f"Brak ACK na '{cmd}' ({desc})."
                    return {"hipot": hipot_result, "gnd": gnd_result}

            if not self._cmd("TEST", wait=0.5):
                hipot_result["error"] = (
                    "TEST — brak ACK. Sprawdź Interlock / DUT / tryb lokalny."
                )
                return {"hipot": hipot_result, "gnd": gnd_result}

            wait_hipot = min(ramp + dwell + 1.5, test_timeout)
            print(f"Czekam {wait_hipot:.1f}s na zakończenie HiPot...")
            time.sleep(wait_hipot)

            hipot_result = self._read_hipot_result(hipot_result)

            if hipot_result["status"] in ("fail", "error"):
                print("HiPot FAIL — pomijam Ground Bond.")
                return {"hipot": hipot_result, "gnd": gnd_result}

            if not has_gnd:
                print("Profil bez Ground Bond — sekwencja zakończona.")
                return {"hipot": hipot_result, "gnd": None}

            # ════════════════════════════════════════════════════
            # KROK 2: Ground Bond
            # ════════════════════════════════════════════════════
            print("\n─── KROK 2: Ground Bond ─────────────────────────────────")

            if relay:
                try:
                    relay.set_pe()
                    state = relay.get_status()
                    print(f"✔ Relay → PE | STATUS: {state}")
                    if state != "PE":
                        gnd_result["error"] = (
                            f"Relay nie przełączył się na PE (STATUS: {state})"
                        )
                        return {"hipot": hipot_result, "gnd": gnd_result}
                except RelayError as e:
                    gnd_result["error"] = f"Relay błąd przed Ground Bond: {e}"
                    return {"hipot": hipot_result, "gnd": gnd_result}
            else:
                print("⚠ relay_port nie skonfigurowany — pomijam relay")

            try:
                gc       = gnd_profile.get("current",   25.0)
                g_hi     = gnd_profile.get("hi_limit",  100)
                g_lo     = gnd_profile.get("lo_limit",  0)
                g_dwell  = gnd_profile.get("dwell",     1.0)
                g_offset = gnd_profile.get("offset",    0)
                g_freq   = gnd_profile.get("frequency", 1)

                gnd_result["_hi_limit"] = g_hi

                if not self._cmd("SS 2", wait=0.3):
                    gnd_result["error"] = "SS 2 — brak ACK."
                    return {"hipot": hipot_result, "gnd": gnd_result}

                if not self._cmd("SAG", wait=0.3):
                    gnd_result["error"] = "SAG — brak ACK."
                    return {"hipot": hipot_result, "gnd": gnd_result}

                for cmd, desc in [
                    (f"EC {gc:.2f}",       f"prąd {gc:.2f} A"),
                    (f"EH {g_hi}",         f"HI limit {g_hi} mΩ"),
                    (f"EL {g_lo}",         f"LO limit {g_lo} mΩ"),
                    (f"EDW {g_dwell:.1f}", f"dwell {g_dwell:.1f} s"),
                    (f"EO {g_offset}",     f"offset {g_offset} mΩ"),
                    (f"EF {g_freq}",       f"częstotliwość"),
                ]:
                    if not self._cmd(cmd, wait=0.3):
                        gnd_result["error"] = f"Brak ACK na '{cmd}' ({desc})."
                        return {"hipot": hipot_result, "gnd": gnd_result}

                if not self._cmd("TEST", wait=0.5):
                    gnd_result["error"] = (
                        "TEST (GND) — brak ACK. Sprawdź CURRENT OUTPUT."
                    )
                    return {"hipot": hipot_result, "gnd": gnd_result}

                wait_gnd = min(g_dwell + 2.5, test_timeout)
                print(f"Czekam {wait_gnd:.1f}s na zakończenie Ground Bond...")
                time.sleep(wait_gnd)

                gnd_result = self._read_gnd_result(gnd_result)
                return {"hipot": hipot_result, "gnd": gnd_result}

            finally:
                if relay:
                    relay.safe_return_to_hipot()
                    print("✔ Relay → HIPOT (powrót po Ground Bond)")

        except HipotError as e:
            hipot_result["error"] = str(e)
            return {"hipot": hipot_result, "gnd": gnd_result}
        except Exception as e:
            hipot_result["error"] = f"Nieoczekiwany błąd: {e}"
            return {"hipot": hipot_result, "gnd": gnd_result}

        finally:
            if relay:
                relay.disconnect()
                print("✔ Relay rozłączony")
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
        result = _empty_result()
        try:
            ramp  = profile.get("ramp",  1.0) if profile else 1.0
            dwell = profile.get("dwell", 2.0) if profile else 2.0

            self._send("RESET", wait=0.3)

            if not self._cmd("SPR 1", wait=0.6):
                result["error"] = "SPR 1 — brak ACK."
                return result
            if not self._cmd("FL 1", wait=0.6):
                result["error"] = "FL 1 — brak ACK."
                return result
            if not self._cmd("TEST", wait=0.4):
                result["error"] = "TEST — brak ACK."
                return result

            wait_time = min(ramp + dwell + 1.5, test_timeout)
            print(f"Czekam {wait_time:.1f}s na zakończenie testu...")
            time.sleep(wait_time)

            return self._read_hipot_result(result)

        except HipotError as e:
            result["error"] = str(e)
            return result
        except Exception as e:
            result["error"] = f"Nieoczekiwany błąd: {e}"
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

    # ── Zachowana kompatybilność wsteczna ──────────────────────────────────
    def program_and_run(self, profile: dict, test_timeout: float = 30.0) -> dict:
        seq = self.run_full_sequence(profile, test_timeout)
        return seq["hipot"]


# ── Helpers ────────────────────────────────────────────────────────────────
def _empty_result() -> dict:
    return {
        "result":     None,
        "voltage":    None,
        "current":    None,
        "time":       None,
        "status":     "error",
        "error":      None,
        "error_code": None,
        "error_desc": None,
    }


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None