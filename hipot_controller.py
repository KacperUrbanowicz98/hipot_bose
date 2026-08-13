"""
hipot_controller.py
-------------------
Sterowanie testerem Slaughter 4320 przez RS-232.

Zmiany bezpieczeństwa względem poprzedniej wersji:

  1. Koniec ze sztywnym sleep(min(ramp+dwell+1.5, test_timeout)).
     min() SKRACAŁ czekanie przy długim profilu — kod czytał wynik i szedł
     dalej, gdy wysokie napięcie mogło być jeszcze podane. Teraz:
     minimum ramp+dwell (fizyka), potem POLLING RD n? aż do wyniku,
     z marginesem jako limitem awaryjnym — nigdy jako skróceniem.

  2. Przekaźnik jest przełączany DOPIERO po potwierdzonym odczycie wyniku
     plus dodatkowe opóźnienie (relay_switch_delay_s). Wcześniej set_pe()
     szło zaraz po sleepie, czyli potencjalnie pod napięciem.

  3. Pozycja przekaźnika jest wymuszana PRZED KAŻDYM testem, gdy relay_port
     jest skonfigurowany — także dla profilu bez Ground Bond. Wcześniej
     korekta działała tylko dla profilu z GND, więc przekaźnik zostawiony
     na PE po nieudanym powrocie kierował 3 kV w tor PE przy następnej sztuce.

  4. Profil wymaga Ground Bond, a relay_port nie jest ustawiony -> TWARDY BŁĄD.
     Wcześniej sekwencja leciała dalej: 25 A przez tor nieprzełączony na PE,
     a jedyne ostrzeżenie to print() niewidoczny w buildzie --windowed.

  5. ABORT działa naprawdę: threading.Event jest sprawdzany między krokami
     i w pętlach czekania, a przerwanie wysyła STOP + RESET i wraca
     przekaźnikiem na HIPOT.

  6. Wynik HiPot inny niż jawne "pass" NIE przepuszcza do Ground Bond.
     Wcześniej status "done" (NAK na RD 1?, nieznany format) przechodził dalej.

  7. Rezystancja i prąd Ground Bond rozpoznawane po zaprogramowanym prądzie
     (gnd_field_order="auto"), bo dokumentacja w repo opisywała kolejność
     pól sprzecznie.
"""

from __future__ import annotations

import threading
import time

import serial
import serial.tools.list_ports

import verdict as V
from app_logging import get_logger
from relay_controller import RelayController, RelayError

log = get_logger(__name__)


FAIL_CODES = {
    "0001": "HI limit przekroczony — prąd za wysoki",
    "0002": "LO limit — prąd za niski (brak kontaktu z DUT?)",
    "0003": "Arc Detection — wykryto wyładowanie łukowe",
    "0004": "Interlock — sprawdź pokrywę / podłączenie DUT",
    "0005": "Timeout testu — DUT nie odpowiedział w czasie",
    "0006": "Ramp Failure — napięcie nie osiągnęło wartości docelowej",
}

HIPOT_STEP = 1
GND_STEP = 2

#: Typy kroku rozpoznawane w odpowiedzi RD n?
HIPOT_TYPES = ("ACW", "DCW", "IR")
GND_TYPE = "GND"


class HipotError(Exception):
    pass


class HipotAborted(HipotError):
    """Test przerwany przez operatora."""
    pass


class HipotTimeout(HipotError):
    """Tester nie zwrócił wyniku w dopuszczalnym czasie."""
    pass


class HipotController:
    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: int = 3,
        relay_port: str | None = None,
        abort_event: threading.Event | None = None,
        hipot_cfg: dict | None = None,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._lock = threading.Lock()
        self._relay_port = relay_port
        self._abort = abort_event or threading.Event()

        cfg = hipot_cfg or {}
        self.result_margin_s = float(cfg.get("result_margin_s", 30.0))
        self.poll_interval_s = float(cfg.get("result_poll_interval_s", 0.5))
        self.relay_switch_delay_s = float(cfg.get("relay_switch_delay_s", 1.0))
        self.require_relay_for_gnd = bool(cfg.get("require_relay_for_gnd", True))
        self.gnd_field_order = str(cfg.get("gnd_field_order", "auto"))
        self.gnd_current_tolerance = float(cfg.get("gnd_current_tolerance", 0.35))

        self.status_query = str(cfg.get("status_query", "SA?"))
        self.status_busy_tokens = [
            str(t).upper() for t in cfg.get("status_busy_tokens", []) or []
        ]
        self.status_idle_tokens = [
            str(t).upper() for t in cfg.get("status_idle_tokens", []) or []
        ]

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
                dsrdtr=False,
            )
            time.sleep(0.3)
            log.info("Połączono z testerem: %s @ %s", self.port, self.baudrate)

        except serial.SerialException as e:
            raise HipotError(f"Błąd otwarcia portu {self.port}: {e}")

    def disconnect(self):
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception as e:
                log.warning("Błąd zamykania portu testera: %s", e)
        self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Abort ──────────────────────────────────────────────────────────────
    def _check_abort(self):
        if self._abort.is_set():
            raise HipotAborted("Test przerwany przez operatora.")

    def _sleep_abortable(self, seconds: float):
        """Sen z reakcją na ABORT co 100 ms."""
        deadline = time.monotonic() + max(0.0, seconds)

        while time.monotonic() < deadline:
            self._check_abort()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def emergency_stop(self):
        """
        Natychmiastowe zdjęcie napięcia. Wołane przy ABORT i przy timeoucie.
        Nie rzuca wyjątków — to jest ścieżka ratunkowa.
        """
        for cmd in ("STOP", "RESET"):
            try:
                if self.is_connected:
                    self._send(cmd, wait=0.3)
                    log.warning("EMERGENCY: wysłano %s", cmd)
            except Exception as e:
                log.error("EMERGENCY: %s nie powiodło się: %s", cmd, e)

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
                log.debug("SEND >> %-20r | RESP << %r", command, resp)
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
                raw = self._serial.read_until(b"\n", size=512)
                log.debug("QUERY >> %-20r | RESP << %r", command, raw)
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
                raw_str = raw_bytes.decode("ascii", errors="replace").strip()
                log.debug("QUERY_RAW >> %-20r | RESP << %r", command, raw_bytes)
                return raw_bytes, raw_str

        except serial.SerialException as e:
            raise HipotError(f"Utrata połączenia RS-232 podczas '{command}': {e}")

    def _cmd(self, command: str, wait: float = 0.6) -> bool:
        self._check_abort()
        resp = self._send(command, wait)

        if b"\x15" in resp:
            raise HipotError(
                f"NAK od testera na komendę '{command}' — komenda odrzucona."
            )

        return b"\x06" in resp

    def _require_ack(self, command: str, description: str, wait: float = 0.3):
        if not self._cmd(command, wait):
            raise HipotError(f"Brak ACK na '{command}' ({description}).")

    # ── Status testera ─────────────────────────────────────────────────────
    def _test_busy(self) -> bool | None:
        """
        Czy tester jest w trakcie testu?

        True  -> na pewno trwa
        False -> na pewno zakończony
        None  -> NIE DA SIĘ USTALIĆ (brak skonfigurowanych tokenów)

        Tokeny busy/idle uzupełnia się w config.json -> hipot.status_busy_tokens
        / status_idle_tokens po podejrzeniu realnych odpowiedzi w zakładce
        Diagnostyka. Dopóki są puste, wynik to None i decyduje polling RD n?.
        """
        if not self.status_busy_tokens and not self.status_idle_tokens:
            return None

        try:
            resp = self._query(self.status_query, wait=0.4).upper()
        except HipotError as e:
            log.warning("Status testera niedostępny: %s", e)
            return None

        if not resp:
            return None

        for token in self.status_busy_tokens:
            if token and token in resp:
                return True

        for token in self.status_idle_tokens:
            if token and token in resp:
                return False

        log.warning("Nierozpoznany status testera: %r", resp)
        return None

    # ── Odczyt wyniku z pollingiem ─────────────────────────────────────────
    @staticmethod
    def _is_nak(raw_bytes: bytes) -> bool:
        return b"\x15" in raw_bytes and len(raw_bytes) <= 4

    @staticmethod
    def _looks_like_result(raw: str) -> bool:
        """Czy odpowiedź wygląda na kompletny rekord wyniku."""
        if not raw:
            return False
        parts = [p.strip() for p in raw.split(",")]
        return len(parts) >= 4 and any(parts)

    def _poll_for_result(self, step: int, min_wait: float,
                         label: str) -> tuple[bytes, str]:
        """
        Czeka na wynik kroku.

        min_wait  -> minimalny czas fizyczny testu (ramp + dwell). Nigdy nie
                     jest skracany.
        margin    -> result_margin_s z konfiguracji, DOPISYWANY do min_wait
                     jako limit awaryjny.

        Po min_wait odpytuje RD <step>? co poll_interval_s. Tester zwraca NAK,
        dopóki wyniku nie ma — czyli polling sam w sobie jest potwierdzeniem
        zakończenia testu, niezależnie od znajomości rejestru statusu.

        Rzuca HipotTimeout po przekroczeniu min_wait + margin.
        """
        cmd = f"RD {step}?"

        log.info("%s: minimalny czas testu %.1f s (margines %.1f s)",
                 label, min_wait, self.result_margin_s)

        self._sleep_abortable(min_wait)

        deadline = time.monotonic() + self.result_margin_s
        attempts = 0
        last_raw = ""

        while time.monotonic() < deadline:
            self._check_abort()
            attempts += 1

            busy = self._test_busy()
            if busy is True:
                log.info("%s: tester nadal zgłasza test w toku — czekam.", label)
                self._sleep_abortable(self.poll_interval_s)
                continue

            raw_bytes, raw = self._query_raw(cmd, wait=0.6)
            last_raw = raw

            if self._is_nak(raw_bytes):
                self._sleep_abortable(self.poll_interval_s)
                continue

            if self._looks_like_result(raw):
                log.info("%s: wynik odebrany po %d odpytaniach: %r",
                         label, attempts, raw)
                return raw_bytes, raw

            self._sleep_abortable(self.poll_interval_s)

        # Nie doczekaliśmy się wyniku — zdejmujemy napięcie.
        self.emergency_stop()

        raise HipotTimeout(
            f"{label}: brak wyniku w {min_wait + self.result_margin_s:.1f} s "
            f"(ostatnia odpowiedź: {last_raw!r}). Wysłano STOP i RESET."
        )

    # ── Parsowanie wyniku HiPot ────────────────────────────────────────────
    def _parse_hipot_result(self, raw: str, result: dict) -> dict:
        parts = [p.strip() for p in raw.split(",")]
        log.info("RD %d? parts (%d): %s", HIPOT_STEP, len(parts), parts)

        # Format: <file>,<step>,<type>,<verdict>,<voltage>,<current>,<time>[,<err>]
        if len(parts) < 7:
            result["raw_result"] = raw
            result["result"] = "Unknown"
            result["status"] = "unknown"
            result["error_desc"] = f"Nieoczekiwana odpowiedź RD {HIPOT_STEP}?: '{raw}'"
            return result

        step_type = parts[2].upper()

        if step_type == GND_TYPE:
            # W slocie 1 leży wynik Ground Bond — parsowanie jako HiPot dałoby
            # bezsensowne napięcie/prąd.
            result["raw_result"] = raw
            result["result"] = "Unknown"
            result["status"] = "unknown"
            result["error_desc"] = (
                f"W slocie {HIPOT_STEP} jest wynik Ground Bond, nie HiPot — "
                "sprawdź kolejność kroków w pamięci testera."
            )
            return result

        if step_type not in HIPOT_TYPES:
            log.warning("Nieznany typ kroku HiPot: %r (kontynuuję parsowanie)",
                        step_type)

        verdict = parts[3]
        result["result"] = verdict
        result["type"] = step_type
        result["voltage"] = parts[4]
        result["current"] = parts[5]
        result["time"] = parts[6]

        error_code = parts[7].strip() if len(parts) > 7 else ""
        if error_code and error_code != "0000":
            result["error_code"] = error_code
            result["error_desc"] = FAIL_CODES.get(
                error_code, f"Nieznany kod błędu: {error_code}"
            )

        if V.is_pass(verdict):
            result["status"] = "pass"

        elif V.is_fail(verdict):
            result["status"] = "fail"

            if not result.get("error_desc"):
                current_val = _safe_float(result["current"])
                hi_limit = result.get("_hi_limit")
                lo_limit = result.get("_lo_limit")

                if (hi_limit is not None and current_val is not None
                        and current_val > hi_limit):
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
            result["status"] = "unknown"
            result["error_desc"] = (
                f"Nierozpoznany werdykt testera: {verdict!r}"
            )

        return result

    # ── Parsowanie wyniku Ground Bond ──────────────────────────────────────
    def _assign_gnd_fields(self, first: str, second: str,
                           expected_current: float) -> tuple[str, str, bool]:
        """
        Zwraca (current, resistance, ambiguous).

        Kolejność pól rezystancja/prąd w odpowiedzi RD n? była w repo opisana
        sprzecznie (hipot_controller vs ground_bond_test). Zamiast zgadywać,
        porównujemy obie liczby z ZAPROGRAMOWANYM prądem GND: to pole, które
        jest bliskie zadanej wartości (np. 25 A), jest prądem.
        """
        order = self.gnd_field_order

        if order == "current_first":
            return first, second, False

        if order == "resistance_first":
            return second, first, False

        a = _safe_float(first)
        b = _safe_float(second)

        if expected_current and expected_current > 0 and a is not None and b is not None:
            tol = abs(expected_current) * self.gnd_current_tolerance
            a_match = abs(a - expected_current) <= tol
            b_match = abs(b - expected_current) <= tol

            if a_match and not b_match:
                return first, second, False

            if b_match and not a_match:
                return second, first, False

        # Nie da się rozstrzygnąć — bierzemy kolejność z Format A i sygnalizujemy.
        log.warning(
            "GND: nie rozpoznano, które pole to prąd (%r, %r) przy zadanym %.2f A. "
            "Przyjmuję current_first. Ustaw hipot.gnd_field_order w config.json "
            "po weryfikacji na stanowisku.",
            first, second, expected_current or 0.0,
        )
        return first, second, True

    def _parse_gnd_result(self, raw: str, gnd_result: dict,
                          expected_current: float) -> dict:
        parts = [p.strip() for p in raw.split(",")]
        log.info("RD %d? parts (%d): %s", GND_STEP, len(parts), parts)

        if len(parts) < 6:
            gnd_result["raw_result"] = raw
            gnd_result["result"] = "Unknown"
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = f"Nieoczekiwana odpowiedź RD {GND_STEP}?: '{raw}'"
            return gnd_result

        upper = [p.upper() for p in parts]

        if upper[2] == GND_TYPE:
            # Format A: <file>,<step>,GND,<verdict>,<X>,<Y>,<time>
            verdict = parts[3]
            first, second = parts[4], parts[5]
            gnd_result["time"] = parts[6] if len(parts) > 6 else "—"

        elif upper[0] == GND_TYPE:
            # Format B: GND,<step>,<verdict>,<X>,<Y>,<time>
            verdict = parts[2]
            first, second = parts[3], parts[4]
            gnd_result["time"] = parts[5]

        elif upper[2] in HIPOT_TYPES:
            gnd_result["raw_result"] = raw
            gnd_result["result"] = "Unknown"
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = (
                f"W slocie {GND_STEP} jest wynik HiPot ({parts[2]}), nie Ground Bond — "
                "sprawdź konfigurację kroków w pamięci testera."
            )
            return gnd_result

        else:
            gnd_result["raw_result"] = raw
            gnd_result["result"] = "Unknown"
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = (
                f"Nierozpoznany format wyniku Ground Bond: '{raw}'"
            )
            return gnd_result

        current, resistance, ambiguous = self._assign_gnd_fields(
            first, second, expected_current
        )

        gnd_result["current"] = current
        gnd_result["resistance"] = resistance
        gnd_result["result"] = verdict
        gnd_result["fields_ambiguous"] = ambiguous

        if V.is_pass(verdict):
            gnd_result["status"] = "pass"

        elif V.is_fail(verdict):
            gnd_result["status"] = "fail"

            res_val = _safe_float(resistance)
            hi_limit = gnd_result.get("_hi_limit")

            if (res_val is not None and hi_limit is not None
                    and res_val > float(hi_limit)):
                gnd_result["error_desc"] = (
                    f"Rezystancja {res_val} mΩ > HI limit {hi_limit} mΩ"
                )
            else:
                gnd_result["error_desc"] = (
                    "FAIL Ground Bond — sprawdź podłączenie PE"
                )

            if ambiguous:
                gnd_result["error_desc"] += (
                    " [uwaga: kolejność pól prąd/rezystancja niepotwierdzona]"
                )
        else:
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = f"Nierozpoznany werdykt GND: {verdict!r}"

        return gnd_result

    # ── Przekaźnik ─────────────────────────────────────────────────────────
    def _open_relay(self) -> RelayController | None:
        """Otwiera połączenie z ESP, jeśli relay_port jest skonfigurowany."""
        if not self._relay_port:
            return None

        relay = RelayController(port=self._relay_port)
        relay.connect()
        return relay

    def _force_relay_home(self, relay: RelayController):
        """
        Wymusza pozycję HIPOT przed startem testu.

        Robimy to dla KAŻDEGO profilu, nie tylko z Ground Bond. Nieudany powrót
        na HIPOT po poprzednim teście zostawiał przekaźnik na PE, a profil bez
        GND w ogóle nie tworzył RelayController — czyli nikt tego nie korygował
        i wysokie napięcie szło torem PE.
        """
        state = relay.get_status()
        log.info("Relay: stan przed testem = %s", state)

        if state == "HIPOT":
            return

        log.warning("Relay nie jest w pozycji HIPOT (%s) — wymuszam korektę.", state)
        relay.set_hipot()

        confirmed = relay.get_status()
        if confirmed != "HIPOT":
            raise RelayError(
                f"Nie udało się ustawić przekaźnika w pozycji HIPOT przed testem "
                f"(stan: {confirmed}). Test zablokowany — wysokie napięcie mogłoby "
                "pójść torem PE."
            )

    # ── Krok 1: HiPot ──────────────────────────────────────────────────────
    def _run_hipot_step(self, profile: dict, result: dict):
        voltage = float(profile.get("voltage", 3.0))
        hi_limit = float(profile.get("hi_limit", 10.0))
        lo_limit = float(profile.get("lo_limit", 0.0))
        ramp = float(profile.get("ramp", 1.0))
        dwell = float(profile.get("dwell", 2.0))
        frequency = profile.get("frequency", 0)

        result["_hi_limit"] = hi_limit
        result["_lo_limit"] = lo_limit

        log.info("─── KROK 1: HiPot AC ───")

        self._require_ack("SS 1", "wybór kroku 1")

        for cmd, desc in [
            (f"EV {voltage:.2f}", f"napięcie {voltage:.2f} kV"),
            (f"EH {hi_limit:.2f}", f"HI limit {hi_limit:.2f} mA"),
            (f"EL {lo_limit:.2f}", f"LO limit {lo_limit:.2f} mA"),
            (f"ERU {ramp:.1f}", f"ramp {ramp:.1f} s"),
            (f"EDW {dwell:.1f}", f"dwell {dwell:.1f} s"),
            (f"EF {frequency}", "częstotliwość"),
        ]:
            self._require_ack(cmd, desc)

        if not self._cmd("TEST", wait=0.5):
            raise HipotError(
                "TEST — brak ACK. Sprawdź Interlock / DUT / tryb lokalny."
            )

        raw_bytes, raw = self._poll_for_result(
            HIPOT_STEP, min_wait=ramp + dwell, label="HiPot"
        )

        self._parse_hipot_result(raw, result)

    # ── Krok 2: Ground Bond ────────────────────────────────────────────────
    def _run_gnd_step(self, gnd_profile: dict, gnd_result: dict,
                      relay: RelayController | None):
        gc = float(gnd_profile.get("current", 25.0))
        g_hi = float(gnd_profile.get("hi_limit", 100))
        g_lo = float(gnd_profile.get("lo_limit", 0))
        g_dwell = float(gnd_profile.get("dwell", 1.0))
        g_offset = gnd_profile.get("offset", 0)
        g_freq = gnd_profile.get("frequency", 1)

        gnd_result["_hi_limit"] = g_hi

        log.info("─── KROK 2: Ground Bond ───")

        # Napięcie zdjęte i wynik odczytany — dopiero teraz wolno ruszyć styki.
        if self.relay_switch_delay_s > 0:
            log.info("Odczekanie %.1f s przed przełączeniem przekaźnika.",
                     self.relay_switch_delay_s)
            self._sleep_abortable(self.relay_switch_delay_s)

        if relay is not None:
            relay.set_pe()
            state = relay.get_status()
            log.info("Relay → PE | STATUS: %s", state)

            if state != "PE":
                raise RelayError(
                    f"Relay nie przełączył się na PE (STATUS: {state}) — "
                    "Ground Bond zablokowany."
                )

        self._require_ack("SS 2", "wybór kroku 2")
        self._require_ack("SAG", "tryb Ground Bond")

        for cmd, desc in [
            (f"EC {gc:.2f}", f"prąd {gc:.2f} A"),
            (f"EH {g_hi}", f"HI limit {g_hi} mΩ"),
            (f"EL {g_lo}", f"LO limit {g_lo} mΩ"),
            (f"EDW {g_dwell:.1f}", f"dwell {g_dwell:.1f} s"),
            (f"EO {g_offset}", f"offset {g_offset} mΩ"),
            (f"EF {g_freq}", "częstotliwość"),
        ]:
            self._require_ack(cmd, desc)

        if not self._cmd("TEST", wait=0.5):
            raise HipotError("TEST (GND) — brak ACK. Sprawdź CURRENT OUTPUT.")

        raw_bytes, raw = self._poll_for_result(
            GND_STEP, min_wait=g_dwell, label="Ground Bond"
        )

        self._parse_gnd_result(raw, gnd_result, expected_current=gc)

    # ── GŁÓWNA METODA ──────────────────────────────────────────────────────
    def run_full_sequence(self, profile: dict, test_timeout: float | None = None) -> dict:
        """
        Sekwencja:
            1. HiPot AC (krok 1)
            2. Ground Bond (krok 2), tylko gdy profile["ground_bond"] != None
               i tylko gdy HiPot zwrócił jawne PASS.

        test_timeout jest przyjmowany dla zgodności wstecznej i traktowany jako
        MARGINES doliczany do ramp+dwell, nigdy jako skrócenie czekania.
        """
        if test_timeout is not None:
            self.result_margin_s = max(float(test_timeout), 5.0)

        gnd_profile = profile.get("ground_bond")
        has_gnd = gnd_profile is not None

        hipot_result = _empty_result()
        gnd_result = _empty_result() if has_gnd else None

        relay = None

        try:
            # ── Przekaźnik: pozycja wyjściowa dla KAŻDEGO testu ────────────
            if self._relay_port:
                try:
                    relay = self._open_relay()
                    self._force_relay_home(relay)
                except RelayError as e:
                    hipot_result["error"] = f"Przekaźnik ESP: {e}"
                    return {"hipot": hipot_result, "gnd": gnd_result}

            elif has_gnd and self.require_relay_for_gnd:
                hipot_result["error"] = (
                    "Profil wymaga Ground Bond, a relay_port nie jest "
                    "skonfigurowany. Test zablokowany — bez przełączenia na PE "
                    "prąd GND popłynąłby niewłaściwym torem. "
                    "Ustaw port ESP w Panelu Inżynieryjnym → Relay (ESP)."
                )
                log.error(hipot_result["error"])
                return {"hipot": hipot_result, "gnd": gnd_result}

            elif has_gnd:
                log.warning(
                    "relay_port nie skonfigurowany, a profil ma Ground Bond. "
                    "require_relay_for_gnd=False — kontynuuję na własne ryzyko."
                )

            # ── Przygotowanie testera ─────────────────────────────────────
            self._check_abort()
            self._send("RESET", wait=0.4)

            self._require_ack("SPR 1", "Remote ON", wait=0.6)
            self._require_ack("FL 1", "File Load", wait=0.3)

            # ── Krok 1 ────────────────────────────────────────────────────
            try:
                self._run_hipot_step(profile, hipot_result)

            except HipotAborted as e:
                hipot_result["aborted"] = True
                hipot_result["status"] = "aborted"
                hipot_result["error"] = str(e)
                self.emergency_stop()
                return {"hipot": hipot_result, "gnd": gnd_result}

            except (HipotError, RelayError) as e:
                hipot_result["error"] = str(e)
                return {"hipot": hipot_result, "gnd": gnd_result}

            # Do Ground Bond przechodzimy WYŁĄCZNIE po jawnym PASS.
            # Wcześniej status "done" (NAK, nieznany format) przechodził dalej.
            if hipot_result.get("status") != "pass":
                log.warning("HiPot: status=%s — Ground Bond pominięty.",
                            hipot_result.get("status"))
                return {"hipot": hipot_result, "gnd": gnd_result}

            if not has_gnd:
                log.info("Profil bez Ground Bond — sekwencja zakończona.")
                return {"hipot": hipot_result, "gnd": None}

            # ── Krok 2 ────────────────────────────────────────────────────
            try:
                self._run_gnd_step(gnd_profile, gnd_result, relay)

            except HipotAborted as e:
                gnd_result["aborted"] = True
                gnd_result["status"] = "aborted"
                gnd_result["error"] = str(e)
                self.emergency_stop()

            except (HipotError, RelayError) as e:
                gnd_result["error"] = str(e)

            finally:
                if relay is not None:
                    relay.safe_return_to_hipot()

            return {"hipot": hipot_result, "gnd": gnd_result}

        except HipotAborted as e:
            hipot_result["aborted"] = True
            hipot_result["status"] = "aborted"
            hipot_result["error"] = str(e)
            self.emergency_stop()
            return {"hipot": hipot_result, "gnd": gnd_result}

        except (HipotError, RelayError) as e:
            hipot_result["error"] = str(e)
            return {"hipot": hipot_result, "gnd": gnd_result}

        except Exception as e:
            log.exception("Nieoczekiwany błąd w sekwencji testowej")
            hipot_result["error"] = f"Nieoczekiwany błąd: {e}"
            return {"hipot": hipot_result, "gnd": gnd_result}

        finally:
            self._shutdown(relay)

    def _shutdown(self, relay):
        """Stan bezpieczny po każdym teście, niezależnie od wyniku."""
        if relay is not None:
            try:
                relay.safe_return_to_hipot()
            except Exception as e:
                log.error("Powrót przekaźnika na HIPOT nieudany: %s", e)

            try:
                relay.disconnect()
            except Exception as e:
                log.warning("Rozłączenie ESP nieudane: %s", e)

        for cmd in ("STOP", "SPR 0", "RESET"):
            try:
                if self.is_connected:
                    self._send(cmd, wait=0.3)
            except Exception as e:
                log.warning("Komenda końcowa %s nieudana: %s", cmd, e)

    # ── Fallback: profil z pamięci urządzenia ──────────────────────────────
    def run_test(self, profile: dict = None, test_timeout: float = 20.0) -> dict:
        result = _empty_result()

        try:
            ramp = float(profile.get("ramp", 1.0)) if profile else 1.0
            dwell = float(profile.get("dwell", 2.0)) if profile else 2.0

            self._send("RESET", wait=0.3)
            self._require_ack("SPR 1", "Remote ON", wait=0.6)
            self._require_ack("FL 1", "File Load", wait=0.6)

            if not self._cmd("TEST", wait=0.4):
                result["error"] = "TEST — brak ACK."
                return result

            raw_bytes, raw = self._poll_for_result(
                HIPOT_STEP, min_wait=ramp + dwell, label="HiPot (fallback)"
            )

            return self._parse_hipot_result(raw, result)

        except HipotAborted as e:
            result["aborted"] = True
            result["status"] = "aborted"
            result["error"] = str(e)
            self.emergency_stop()
            return result

        except HipotError as e:
            result["error"] = str(e)
            return result

        except Exception as e:
            log.exception("Nieoczekiwany błąd w run_test")
            result["error"] = f"Nieoczekiwany błąd: {e}"
            return result

        finally:
            self._shutdown(None)

    # ── Zgodność wsteczna ──────────────────────────────────────────────────
    def program_and_run(self, profile: dict, test_timeout: float = 30.0) -> dict:
        seq = self.run_full_sequence(profile, test_timeout)
        return seq["hipot"]


# ── Helpers ────────────────────────────────────────────────────────────────
def _empty_result() -> dict:
    return {
        "result": None,
        "type": None,
        "voltage": None,
        "current": None,
        "resistance": None,
        "time": None,
        "status": "error",
        "error": None,
        "error_code": None,
        "error_desc": None,
        "raw_result": None,
        "aborted": False,
        "fields_ambiguous": False,
    }


def _safe_float(value) -> float | None:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return None
