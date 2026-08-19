"""
hipot_controller.py
-------------------
Sterowanie testerem Slaughter 4320 przez RS-232.

═══════════════════════════════════════════════════════════════════════════
POPRAWKI 1.1.2 — po analizie logu ze stanowiska + manuala Slaughter 4000
═══════════════════════════════════════════════════════════════════════════

  KRYTYCZNE: tester NIE zwraca słowa "Fail". Zwraca opisowy status:
  HI-Limit, LO-Limit, OFL (log stanowiskowy 2026-08-17 + manual s. 18-19).
  Wersja 1.1.1 rozpoznawała tylko Pass/Fail, więc rekord każdej sztuki
  NIEZALICZONEJ był odrzucany przez polling -> timeout -> ERROR zamiast FAIL.
  Tokeny statusu są teraz w verdict.py (jedno źródło) i obejmują warianty
  RS-232 (HI-Limit) oraz wyświetlacza (HI-Lmt).

  Opisy przyczyn wprost z manuala:
    OFL bez odczytu napięcia -> ZWARCIE w DUT
    OFL z odczytem napięcia  -> przeskok / przebicie (flash over)
    HI-Limit z prądem ">20.0" -> poza zakresem 20 mA, bez zwarcia i przeskoku
    GND HI-Limit = granica zakresu (510/200/150 mΩ wg prądu) -> "poza zakresem"

  gnd_field_order domyślnie "current_first" — POTWIERDZONE manualem
  (wyświetlacz GND "30.0A GND 150mΩ") i logiem (24,90 A przy zadanych 25,0 A).

  _safe_float radzi sobie z ">20.0", "<1.00" i jednostkami w polu.

  Ground Bond z zapasem <= 10% do limitu jest oznaczany flagą "marginal" —
  na stanowisku odczyty 95/98/98 mΩ przy limicie 100 mΩ poprzedziły FAIL 106.

═══════════════════════════════════════════════════════════════════════════
POPRAWKI 1.1.1 — po pierwszym uruchomieniu na stanowisku
═══════════════════════════════════════════════════════════════════════════

  A. REGRES NAPRAWIONY: wynik Ground Bond szukany ZNOWU W DWÓCH SLOTACH.
     Wersja 1.1.0 odpytywała tylko RD 2?. Slaughter 4320 na tym stanowisku
     odkłada wynik GND pod RD 1?, więc leciał NAK aż do timeoutu — stąd
     brak odczytanych wartości i 30 sekund czekania.
     Teraz każdy przebieg pollingu sprawdza RD 2?, a potem RD 1?.

  B. Parsowanie po POZYCJI WERDYKTU, nie po sztywnych indeksach.
     Kod szuka pola z 'Pass'/'Fail' i liczy pozostałe pola względem niego.
     Obsługuje wszystkie warianty formatu, także bez znacznika GND:
        1,2,GND,Pass,25.1,65,1.0      (7 pól, znacznik w środku)
        GND,2,Pass,25.1,65,1.0        (6 pól, znacznik z przodu)
        1,2,Pass,25.1,65,1.0          (6 pól, bez znacznika)
        2,Pass,25.1,65,1.0            (5 pól)

  C. HEARTBEAT do ESP w czasie oczekiwania na wynik.
     Firmware ma WATCHDOG_TIMEOUT_MS 30000 — po 30 s bez komendy ESP sam
     wraca na HIPOT. Podczas Ground Bond ostatnią komendą było STATUS? zaraz
     po set_pe(), więc długie czekanie na wynik groziło przełączeniem styków
     POD PRĄDEM 25 A. Teraz w pętli czekania leci PING co ~2 s.

  D. Krótsze marginesy: HiPot 10 s, Ground Bond 6 s (osobny parametr).
     Polling zwraca wynik od razu, gdy tester go odda — margines dotyczy
     tylko sytuacji awaryjnej.

  E. Ruch RS-232 logowany na poziomie INFO, żeby dało się diagnozować
     z logs/app.log bez przebudowy z konsolą.

═══════════════════════════════════════════════════════════════════════════
Poprawki 1.1.0 (bez zmian)
═══════════════════════════════════════════════════════════════════════════
  - koniec z min(ramp+dwell+1.5, test_timeout), który SKRACAŁ czekanie,
  - przekaźnik przełączany dopiero po potwierdzonym odczycie wyniku,
  - pozycja HIPOT wymuszana przed KAŻDYM testem,
  - brak relay_port przy profilu z GND = twardy błąd,
  - ABORT wysyła STOP + RESET i wraca przekaźnikiem na HIPOT,
  - wynik inny niż jawny 'pass' nie przepuszcza do Ground Bond.
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


#: Numeryczne kody błędu — zachowane dla zgodności. Slaughter 4320 przekazuje
#: przyczynę w polu STATUSU (HI-Limit / LO-Limit / OFL), nie osobnym kodem.
FAIL_CODES = {
    "0001": "HI limit przekroczony — prąd za wysoki",
    "0002": "LO limit — prąd za niski (brak kontaktu z DUT?)",
    "0003": "Arc Detection — wykryto wyładowanie łukowe",
    "0004": "Interlock — sprawdź pokrywę / podłączenie DUT",
    "0005": "Timeout testu — DUT nie odpowiedział w czasie",
    "0006": "Ramp Failure — napięcie nie osiągnęło wartości docelowej",
}

#: Zakresy pomiarowe rezystancji GND zależne od prądu (manual, str. 19).
#: Odczyt równy górnej granicy zakresu znaczy "poza zakresem", a nie
#: "zmierzono dokładnie tyle".
GND_METER_RANGES = ((10.0, 510.0), (25.0, 200.0), (30.0, 150.0))


def gnd_meter_range(current: float | None) -> float | None:
    """Górna granica zakresu pomiarowego rezystancji dla danego prądu GND."""
    if current is None:
        return None
    for max_current, max_resistance in GND_METER_RANGES:
        if current <= max_current:
            return max_resistance
    return None


def describe_hipot_status(status: str, voltage: str, current: str,
                          hi_limit=None, lo_limit=None) -> str:
    """
    Opis przyczyny niezaliczenia HiPot na podstawie statusu z testera.

    Rozróżnienia wprost z manuala (Failure Mode Displays, str. 18):
      OFL + napięcie '----'  -> zwarcie w DUT
      OFL + odczyt napięcia  -> przeskok / przebicie (flash over)
      HI-Limit + '>20.0'     -> prąd poza zakresem 20 mA, bez zwarcia
                                i bez przeskoku
    """
    token = V.norm(status)
    volt_txt = str(voltage or "").strip()
    curr_txt = str(current or "").strip()

    out_of_range = curr_txt.startswith(">")
    no_voltage = (not volt_txt) or set(volt_txt) <= set("-. ")

    if token in ("ofl", "overflow"):
        if no_voltage:
            return ("OFL — ZWARCIE w DUT (brak odczytu napięcia). "
                    "Sprawdź DUT i oprzyrządowanie przed powtórzeniem.")
        return (f"OFL — przeskok/przebicie w DUT przy {volt_txt} kV "
                f"(prąd {curr_txt} mA poza zakresem 20 mA).")

    if token in ("hi-limit", "hi-lmt", "hilimit", "hi limit", "hilmt"):
        if out_of_range:
            return (f"HI-Limit — prąd {curr_txt} mA poza zakresem pomiarowym "
                    "20 mA (bez zwarcia i bez przeskoku).")
        limit_txt = f" > HI limit {hi_limit} mA" if hi_limit is not None else ""
        return f"HI-Limit — prąd upływu {curr_txt} mA{limit_txt}."

    if token in ("lo-limit", "lo-lmt", "lolimit", "lo limit", "lolmt"):
        limit_txt = f" < LO limit {lo_limit} mA" if lo_limit is not None else ""
        return (f"LO-Limit — prąd {curr_txt} mA{limit_txt}. "
                "Najczęstsza przyczyna: brak kontaktu z DUT.")

    if token in ("abort", "aborted"):
        return "Test przerwany na testerze (RESET / Interlock)."

    # Generyczne "Fail" nie wnosi nic ponad to, co wywołujący ustali z kodu
    # błędu i porównania z limitem — zwracamy puste, żeby nie nadpisać
    # dokładniejszego opisu.
    if token in ("fail", "failed", "nok"):
        return ""

    if V.is_fail(token):
        return f"Tester zgłosił status: {status}"

    return ""


def describe_gnd_status(status: str, resistance: str, current: str,
                        hi_limit=None, lo_limit=None) -> str:
    """Opis przyczyny niezaliczenia Ground Bond (manual, str. 19)."""
    token = V.norm(status)
    res_val = _safe_float(resistance)
    curr_val = _safe_float(current)
    meter_max = gnd_meter_range(curr_val)

    if token in ("hi-limit", "hi-lmt", "hilimit", "hi limit", "hilmt"):
        if (res_val is not None and meter_max is not None
                and res_val >= meter_max):
            return (f"HI-Limit — rezystancja {res_val} mΩ osiągnęła GÓRNĄ "
                    f"GRANICĘ ZAKRESU ({meter_max:.0f} mΩ przy {curr_val} A). "
                    "To znaczy 'poza zakresem', nie zmierzoną wartość — "
                    "sprawdź podłączenie PE i kabel prądowy.")
        if res_val is not None and hi_limit is not None:
            return (f"HI-Limit — rezystancja {res_val} mΩ > HI limit "
                    f"{hi_limit} mΩ.")
        return f"HI-Limit — rezystancja {resistance} mΩ powyżej limitu."

    if token in ("lo-limit", "lo-lmt", "lolimit", "lo limit", "lolmt"):
        limit_txt = f" < LO limit {lo_limit} mΩ" if lo_limit is not None else ""
        return f"LO-Limit — rezystancja {resistance} mΩ{limit_txt}."

    if token in ("ofl", "overflow"):
        return ("OFL — rezystancja poza zakresem pomiarowym. "
                "Sprawdź ciągłość toru PE.")

    if token in ("abort", "aborted"):
        return "Ground Bond przerwany na testerze (RESET / Interlock)."

    if token in ("fail", "failed", "nok"):
        return ""

    if V.is_fail(token):
        return f"Tester zgłosił status Ground Bond: {status}"

    return ""

HIPOT_STEP = 1
GND_STEP = 2

HIPOT_TYPES = ("ACW", "DCW", "IR")
GND_TYPE = "GND"

#: Slaughter 4320 na tym stanowisku odkłada wynik Ground Bond raz pod RD 2?,
#: raz pod RD 1? (zależnie od tego, jak został wysłany TEST). Sprawdzamy oba.
GND_SLOTS = (f"RD {GND_STEP}?", f"RD {HIPOT_STEP}?")
HIPOT_SLOTS = (f"RD {HIPOT_STEP}?",)

#: Odstęp między kolejnymi PING do ESP w czasie czekania na wynik.
#: Musi być wyraźnie mniejszy niż WATCHDOG_TIMEOUT_MS w firmware (30 s).
HEARTBEAT_INTERVAL_S = 2.0

# ══════════════════════════════════════════════════════════════════════════
# KOMENDY POTWIERDZONE W MANUALU (Slaughter 4000 Series, str. 46-47)
# ══════════════════════════════════════════════════════════════════════════
# 'SA?' NIE ISTNIEJE w tej serii — dlatego zwracało pustkę na stanowisku.
# Udokumentowane komendy zapytań: TD?, RD <n>?, RR?, RI?, LS?, LS <n>?
# Po RS-232 działają też: *IDN?, *ESR?, *ESE, *ESE?, *STB?
#
# *STB? daje LICZBOWY bajt statusu — pewniejszy niż dopasowywanie tekstu:
CMD_IDENTIFY = "*IDN?"        # SLA, model, nr seryjny, firmware
CMD_STATUS_BYTE = "*STB?"     # bajt statusu, bity poniżej
CMD_OPC = "*OPC?"             # 1 = test zakończony, 0 = test w toku
CMD_INTERLOCK = "RI?"         # 1 = Interlock OTWARTY (brak możliwości wyjścia)
CMD_REMOTE_RESET = "RR?"      # 0 = tester trzymany w resecie
CMD_TEST_DATA = "TD?"         # dane bieżącego/ostatniego testu

STB_ALL_PASS = 0x01
STB_FAIL = 0x02
STB_ABORT = 0x04
STB_PROCESS = 0x08            # <- test W TOKU
STB_MESSAGE = 0x10
STB_EVENT = 0x20
STB_SERVICE = 0x40
STB_PROMPT = 0x80


def describe_status_byte(value: int) -> str:
    """Rozpisanie bajtu statusu na czytelne flagi."""
    flags = [
        (STB_ALL_PASS, "ALL_PASS"),
        (STB_FAIL, "FAIL"),
        (STB_ABORT, "ABORT"),
        (STB_PROCESS, "PROCESS"),
        (STB_MESSAGE, "MSG"),
        (STB_EVENT, "EVENT"),
        (STB_SERVICE, "SRQ"),
        (STB_PROMPT, "PROMPT"),
    ]
    active = [name for bit, name in flags if value & bit]
    return f"0x{value:02X} [{', '.join(active) if active else 'brak flag'}]"

#: Tokeny statusu z verdict.py — Slaughter zwraca HI-Limit / LO-Limit / OFL,
#: nie "Fail". Wersja 1.1.1 tego nie rozpoznawała, więc polling odrzucał
#: rekord każdej sztuki niezaliczonej i kończył się timeoutem.
_VERDICT_TOKENS = V.ALL_VERDICT_TOKENS


class HipotError(Exception):
    pass


class HipotAborted(HipotError):
    """Test przerwany przez operatora."""
    pass


class HipotTimeout(HipotError):
    """Tester nie zwrócił wyniku w dopuszczalnym czasie."""
    pass


# ── Parsowanie rekordów ────────────────────────────────────────────────────
def _split(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",")]


def _is_verdict_token(part: str) -> bool:
    return str(part or "").strip().lower() in _VERDICT_TOKENS


def _verdict_index(parts: list[str]) -> int | None:
    """
    Pozycja pola z werdyktem ('Pass'/'Fail') w rekordzie.

    Liczenie pozostałych pól WZGLĘDEM werdyktu jest odporne na to, czy tester
    dorzucił numer pliku, numer kroku i znacznik typu, czy nie. Poprzednia
    wersja miała sztywne indeksy i przy każdym odstępstwie od jednego
    z dwóch znanych formatów zwracała 'Unknown'.
    """
    for i, part in enumerate(parts):
        if _is_verdict_token(part):
            return i
    return None


def _record_type(parts: list[str], vi: int) -> str:
    """Znacznik typu kroku, jeśli jest — leży bezpośrednio przed werdyktem."""
    if vi >= 1:
        candidate = parts[vi - 1].upper()
        if candidate == GND_TYPE or candidate in HIPOT_TYPES:
            return candidate

    # Format B: GND,<step>,<verdict>,...  — znacznik na początku
    if parts and parts[0].upper() in (GND_TYPE,) + HIPOT_TYPES:
        return parts[0].upper()

    return ""


def _is_gnd_record(raw: str) -> bool:
    parts = _split(raw)
    vi = _verdict_index(parts)

    if vi is None:
        return False

    return _record_type(parts, vi) == GND_TYPE


def _is_hipot_record(raw: str) -> bool:
    parts = _split(raw)
    vi = _verdict_index(parts)

    if vi is None:
        return False

    return _record_type(parts, vi) in HIPOT_TYPES


def _is_plausible_record(raw: str) -> bool:
    """
    Rekord z werdyktem i co najmniej dwiema wartościami po nim, ale bez
    znacznika typu. Używane jako zapasowa akceptacja, gdy tester nie
    dokleja 'GND'/'ACW'.
    """
    parts = _split(raw)
    vi = _verdict_index(parts)

    if vi is None:
        return False

    if _record_type(parts, vi):
        return False

    return len(parts) - vi >= 3


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
        self.result_margin_s = float(cfg.get("result_margin_s", 10.0))
        self.gnd_result_margin_s = float(
            cfg.get("gnd_result_margin_s", min(6.0, self.result_margin_s))
        )
        self.poll_interval_s = float(cfg.get("result_poll_interval_s", 0.3))
        self.poll_query_wait_s = float(cfg.get("result_poll_query_wait_s", 0.5))
        self.relay_switch_delay_s = float(cfg.get("relay_switch_delay_s", 1.0))
        self.require_relay_for_gnd = bool(cfg.get("require_relay_for_gnd", True))
        self.gnd_field_order = str(cfg.get("gnd_field_order", "current_first"))
        self.gnd_current_tolerance = float(cfg.get("gnd_current_tolerance", 0.35))
        self.sweeps_before_fallback = int(cfg.get("sweeps_before_fallback", 3))

        # TD? zamiast nieistniejącego SA? — patrz komentarz przy CMD_*.
        self.status_query = str(cfg.get("status_query", CMD_TEST_DATA))
        self.check_interlock_enabled = bool(cfg.get("check_interlock", True))

        # Co ile obiegów pollingu odpytywać stan testera. Sam polling RD n?
        # jest już sygnałem zakończenia, więc *STB?/*OPC? to dodatkowe
        # potwierdzenie — nie ma po co płacić za nie w każdym obiegu.
        self.status_check_every = max(1, int(cfg.get("status_check_every", 3)))

        # Wykryte możliwości firmware. None = jeszcze nie sprawdzone.
        # Po pierwszej nieudanej próbie przestajemy pytać, żeby nie dokładać
        # ruchu na RS-232 przy każdym obiegu.
        self._stb_supported: bool | None = None
        self._opc_supported: bool | None = None
        self.verify_with_status_byte = bool(
            cfg.get("verify_with_status_byte", False)
        )
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

    def _sleep_abortable(self, seconds: float, heartbeat=None):
        """
        Sen z reakcją na ABORT co 100 ms i heartbeatem do ESP co ~2 s.

        Heartbeat jest konieczny, bo firmware ESP ma własny watchdog
        (WATCHDOG_TIMEOUT_MS 30000) i po tym czasie sam wraca na HIPOT.
        W trakcie Ground Bond oznaczałoby to przełączenie styków pod prądem.
        """
        deadline = time.monotonic() + max(0.0, seconds)
        next_beat = time.monotonic() + HEARTBEAT_INTERVAL_S

        while time.monotonic() < deadline:
            self._check_abort()

            if heartbeat is not None and time.monotonic() >= next_beat:
                self._beat(heartbeat)
                next_beat = time.monotonic() + HEARTBEAT_INTERVAL_S

            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _beat(heartbeat):
        """Heartbeat nigdy nie może wywrócić testu."""
        try:
            heartbeat()
        except Exception as e:
            log.warning("Heartbeat do ESP nieudany: %s", e)

    def emergency_stop(self):
        """Natychmiastowe zdjęcie napięcia. Nie rzuca wyjątków."""
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
                log.info("SEND >> %-18s | RESP << %r", command, resp)
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
                log.info("QUERY >> %-18s | RESP << %r", command, raw)
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
                log.info("QUERY_RAW >> %-14s | RESP << %r", command, raw_bytes)
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

    # ── Identyfikacja i stan testera ───────────────────────────────────────
    def identify(self) -> str:
        """
        *IDN? — jedyny pewny sposób sprawdzenia, czy tester odpowiada.

        Zwraca np. 'SLA,4320,1234567,1.23'. Pusty string = brak komunikacji.
        RESET nie nadaje się do tego celu, bo z definicji NIE odpowiada.
        """
        try:
            return self._query(CMD_IDENTIFY, wait=0.6)
        except HipotError as e:
            log.warning("*IDN? nieudane: %s", e)
            return ""

    def _read_status_byte(self) -> int | None:
        """
        *STB? — liczbowy bajt statusu. None, gdy nie da się odczytać.

        Pewniejszy niż dopasowywanie tekstu, bo bit PROCESS (0x08) jednoznacznie
        mówi, czy test trwa, a bity ALL_PASS / FAIL / ABORT dają niezależne
        potwierdzenie wyniku.
        """
        if self._stb_supported is False:
            return None

        try:
            raw = self._query(CMD_STATUS_BYTE, wait=0.3)
        except HipotError as e:
            log.warning("*STB? nieudane: %s", e)
            self._stb_supported = False
            return None

        text = "".join(ch for ch in raw if ch.isalnum()).strip()

        if not text:
            if self._stb_supported is None:
                log.info("Tester nie odpowiada na *STB? — przestaję pytać.")
            self._stb_supported = False
            return None

        try:
            if text.upper().endswith("H"):
                value = int(text[:-1], 16)
            else:
                value = int(text)
        except ValueError:
            try:
                value = int(text, 16)
            except ValueError:
                if self._stb_supported is None:
                    log.warning("*STB? zwrócił nieliczbową odpowiedź %r — "
                                "przestaję pytać.", raw)
                self._stb_supported = False
                return None

        self._stb_supported = True
        return value

    def check_interlock(self) -> bool | None:
        """
        RI? — 1 = Interlock OTWARTY, tester nie wygeneruje wyjścia.

        Sprawdzane PRZED komendą TEST, żeby zamiast bezużytecznego
        'TEST — brak ACK' operator dostał konkretną przyczynę.

        True = otwarty (blokada), False = zamknięty, None = nie ustalono.
        """
        try:
            raw = self._query(CMD_INTERLOCK, wait=0.4).strip()
        except HipotError as e:
            log.warning("RI? nieudane: %s", e)
            return None

        if raw.startswith("1"):
            return True
        if raw.startswith("0"):
            return False

        log.warning("RI? zwrócił nieoczekiwaną odpowiedź: %r", raw)
        return None

    def _assert_ready_for_test(self, label: str):
        """Kontrola przed każdą komendą TEST."""
        if not self.check_interlock_enabled:
            return

        interlock = self.check_interlock()

        if interlock is True:
            raise HipotError(
                f"{label}: INTERLOCK OTWARTY — tester nie wygeneruje napięcia. "
                "Sprawdź wtyczkę interlockową z tyłu testera i pokrywę "
                "stanowiska."
            )

        if interlock is None:
            log.warning("%s: nie udało się odczytać stanu interlocka (RI?).",
                        label)

    # ── Status testera ─────────────────────────────────────────────────────
    def _test_busy(self) -> bool | None:
        """
        True = trwa, False = zakończony, None = nie da się ustalić.

        Kolejność źródeł, od najpewniejszego:
          1. *STB? bit PROCESS (0x08) — liczbowo, bez zgadywania tekstu
          2. *OPC? — 0 = test w toku, 1 = zakończony
          3. tokeny tekstowe z config.json (status_busy_tokens / idle_tokens)
             odpytywane komendą status_query (domyślnie TD?)

        Punkty 1 i 2 są udokumentowane w manualu, więc działają bez
        konfigurowania czegokolwiek na stanowisku.
        """
        stb = self._read_status_byte()

        if stb is not None:
            log.info("*STB? = %s", describe_status_byte(stb))

            if stb & STB_PROCESS:
                return True

            if stb & (STB_ALL_PASS | STB_FAIL | STB_ABORT):
                return False

        if self._opc_supported is not False:
            try:
                opc = self._query(CMD_OPC, wait=0.3).strip()

                if opc.startswith("0"):
                    self._opc_supported = True
                    return True
                if opc.startswith("1"):
                    self._opc_supported = True
                    return False

                if self._opc_supported is None:
                    log.info("Tester nie odpowiada na *OPC? (%r) — "
                             "przestaję pytać.", opc)
                self._opc_supported = False

            except HipotError as e:
                log.warning("*OPC? nieudane: %s", e)
                self._opc_supported = False

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
    def _looks_like_record(raw: str) -> bool:
        return bool(raw) and len(_split(raw)) >= 4

    def _poll_for_result(
        self,
        slots: tuple[str, ...],
        min_wait: float,
        margin: float,
        label: str,
        accept,
        accept_fallback=None,
        heartbeat=None,
    ) -> tuple[str, str]:
        """
        Czeka na wynik kroku, odpytując po kolei wszystkie slots.

        min_wait -> minimalny czas fizyczny testu (ramp + dwell). Nigdy nie
                    jest skracany.
        margin   -> limit awaryjny DOPISYWANY do min_wait.

        Tester zwraca NAK, dopóki wyniku nie ma, więc polling sam potwierdza
        zakończenie testu — bez znajomości rejestru statusu.

        Zwraca (slot, raw). Rzuca HipotTimeout po przekroczeniu limitu.
        """
        log.info("%s: minimalny czas testu %.1f s, margines %.1f s, slots=%s",
                 label, min_wait, margin, list(slots))

        self._sleep_abortable(min_wait, heartbeat=heartbeat)

        deadline = time.monotonic() + margin
        sweep = 0
        fallback: tuple[str, str] | None = None
        last_seen: dict[str, str] = {}

        while time.monotonic() < deadline:
            self._check_abort()
            sweep += 1

            if heartbeat is not None:
                self._beat(heartbeat)

            # Stan testera sprawdzamy co N obiegów — polling RD n? i tak
            # zwraca NAK, dopóki wyniku nie ma, więc to tylko dodatkowe
            # potwierdzenie. Odpytywanie w każdym obiegu wydłużało odczyt.
            if sweep == 1 or sweep % self.status_check_every == 0:
                if self._test_busy() is True:
                    log.info("%s: tester zgłasza test w toku — czekam.", label)
                    self._sleep_abortable(self.poll_interval_s,
                                          heartbeat=heartbeat)
                    continue

            for slot in slots:
                self._check_abort()

                raw_bytes, raw = self._query_raw(
                    slot, wait=self.poll_query_wait_s
                )

                if self._is_nak(raw_bytes):
                    continue

                if not self._looks_like_record(raw):
                    continue

                last_seen[slot] = raw

                if accept(raw):
                    log.info("%s: wynik znaleziony w %s (przebieg %d): %r",
                             label, slot, sweep, raw)
                    return slot, raw

                if (fallback is None and accept_fallback is not None
                        and accept_fallback(raw)):
                    fallback = (slot, raw)

            if fallback is not None and sweep >= self.sweeps_before_fallback:
                log.warning(
                    "%s: brak rekordu z jednoznacznym znacznikiem typu. "
                    "Przyjmuję rekord z %s: %r. Sprawdź format odpowiedzi "
                    "testera w logu.", label, fallback[0], fallback[1]
                )
                return fallback

            self._sleep_abortable(self.poll_interval_s, heartbeat=heartbeat)

        # Nie doczekaliśmy się wyniku — zdejmujemy napięcie.
        self.emergency_stop()

        seen_text = (
            "; ".join(f"{slot}={raw!r}" for slot, raw in last_seen.items())
            or "wszystkie slots zwracały NAK"
        )

        raise HipotTimeout(
            f"{label}: brak wyniku w {min_wait + margin:.1f} s. "
            f"Odpytane slots: {', '.join(slots)}. Odebrano: {seen_text}. "
            "Wysłano STOP i RESET."
        )

    # ── Parsowanie wyniku HiPot ────────────────────────────────────────────
    def _parse_hipot_result(self, raw: str, result: dict) -> dict:
        parts = _split(raw)
        vi = _verdict_index(parts)

        log.info("HiPot parts (%d): %s | idx werdyktu=%s", len(parts), parts, vi)

        result["raw_result"] = raw

        if vi is None:
            result["result"] = "Unknown"
            result["status"] = "unknown"
            result["error_desc"] = (
                f"Brak pola Pass/Fail w odpowiedzi testera: '{raw}'"
            )
            return result

        step_type = _record_type(parts, vi)

        if step_type == GND_TYPE:
            result["result"] = "Unknown"
            result["status"] = "unknown"
            result["error_desc"] = (
                "W slocie HiPot leży wynik Ground Bond — sprawdź kolejność "
                "kroków w pamięci testera."
            )
            return result

        after = parts[vi + 1:]

        if len(after) < 2:
            result["result"] = "Unknown"
            result["status"] = "unknown"
            result["error_desc"] = (
                f"Rekord HiPot bez wartości pomiarowych: '{raw}'"
            )
            return result

        verdict = parts[vi]

        result["result"] = verdict
        result["type"] = step_type or "?"
        result["voltage"] = after[0]
        result["current"] = after[1]
        result["time"] = after[2] if len(after) > 2 else "—"

        error_code = after[3].strip() if len(after) > 3 else ""
        if error_code and error_code != "0000":
            result["error_code"] = error_code
            result["error_desc"] = FAIL_CODES.get(
                error_code, f"Nieznany kod błędu: {error_code}"
            )

        if V.is_pass(verdict):
            result["status"] = "pass"

        elif V.is_abort(verdict):
            result["status"] = "aborted"
            result["aborted"] = True
            result["error_desc"] = describe_hipot_status(
                verdict, result["voltage"], result["current"]
            )

        elif V.is_fail(verdict):
            result["status"] = "fail"

            # Opis z pola STATUSU testera — dokładniejszy niż nasze porównanie
            # z limitem, bo rozróżnia zwarcie od przeskoku i od przekroczenia
            # zakresu pomiarowego (manual, str. 18).
            described = describe_hipot_status(
                verdict,
                result["voltage"],
                result["current"],
                hi_limit=result.get("_hi_limit"),
                lo_limit=result.get("_lo_limit"),
            )

            if described:
                result["error_desc"] = described

            elif not result.get("error_desc"):
                # Generyczne "Fail" bez kodu błędu — ustalamy przyczynę
                # z porównania pomiaru z limitami profilu.
                current_val = _safe_float(result["current"])
                hi_limit = _safe_float(result.get("_hi_limit"))
                lo_limit = _safe_float(result.get("_lo_limit"))

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
                    result["error_desc"] = f"FAIL — status testera: {verdict}"
        else:
            result["status"] = "unknown"
            result["error_desc"] = f"Nierozpoznany status testera: {verdict!r}"

        return result

    # ── Parsowanie wyniku Ground Bond ──────────────────────────────────────
    def _assign_gnd_fields(self, first: str, second: str,
                           expected_current: float) -> tuple[str, str, bool]:
        """
        Zwraca (current, resistance, niejednoznaczne).

        Kolejność pól rezystancja/prąd była w repo opisana sprzecznie
        (hipot_controller vs ground_bond_test), dlatego zamiast zgadywać
        porównujemy obie liczby z ZAPROGRAMOWANYM prądem GND.
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

        log.warning(
            "GND: nie rozpoznano, które pole to prąd (%r, %r) przy zadanym %.2f A. "
            "Przyjmuję current_first. Ustaw hipot.gnd_field_order w config.json.",
            first, second, expected_current or 0.0,
        )
        return first, second, True

    def _parse_gnd_result(self, raw: str, gnd_result: dict,
                          expected_current: float) -> dict:
        parts = _split(raw)
        vi = _verdict_index(parts)

        log.info("GND parts (%d): %s | idx werdyktu=%s", len(parts), parts, vi)

        gnd_result["raw_result"] = raw

        if vi is None:
            gnd_result["result"] = "Unknown"
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = (
                f"Brak pola Pass/Fail w odpowiedzi testera: '{raw}'"
            )
            return gnd_result

        step_type = _record_type(parts, vi)

        if step_type in HIPOT_TYPES:
            gnd_result["result"] = "Unknown"
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = (
                f"W slocie Ground Bond leży wynik HiPot ({step_type}) — "
                "sprawdź konfigurację kroków w pamięci testera."
            )
            return gnd_result

        after = parts[vi + 1:]

        if len(after) < 2:
            gnd_result["result"] = "Unknown"
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = (
                f"Rekord Ground Bond bez wartości pomiarowych: '{raw}'"
            )
            return gnd_result

        verdict = parts[vi]

        current, resistance, ambiguous = self._assign_gnd_fields(
            after[0], after[1], expected_current
        )

        gnd_result["current"] = current
        gnd_result["resistance"] = resistance
        gnd_result["time"] = after[2] if len(after) > 2 else "—"
        gnd_result["result"] = verdict
        gnd_result["fields_ambiguous"] = ambiguous

        if not step_type:
            log.warning("GND: rekord bez znacznika 'GND' — %r", raw)

        if V.is_pass(verdict):
            gnd_result["status"] = "pass"

            # Ostrzeżenie o wyniku blisko limitu — na stanowisku odczyty
            # 95/98/98 mΩ przy limicie 100 mΩ poprzedziły FAIL 106 mΩ.
            res_val = _safe_float(resistance)
            hi_limit = _safe_float(gnd_result.get("_hi_limit"))

            if (res_val is not None and hi_limit and hi_limit > 0
                    and res_val >= 0.9 * hi_limit):
                margin = 100.0 * (hi_limit - res_val) / hi_limit
                gnd_result["marginal"] = True
                gnd_result["error_desc"] = (
                    f"UWAGA: {res_val} mΩ to {margin:.0f}% zapasu do limitu "
                    f"{hi_limit:.0f} mΩ. Sprawdź offset GND i stan kabla PE."
                )
                log.warning("Ground Bond blisko limitu: %s mΩ / %s mΩ",
                            res_val, hi_limit)

        elif V.is_abort(verdict):
            gnd_result["status"] = "aborted"
            gnd_result["aborted"] = True
            gnd_result["error_desc"] = describe_gnd_status(
                verdict, resistance, current
            )

        elif V.is_fail(verdict):
            gnd_result["status"] = "fail"

            described = describe_gnd_status(
                verdict, resistance, current,
                hi_limit=gnd_result.get("_hi_limit"),
                lo_limit=gnd_result.get("_lo_limit"),
            )

            if not described:
                # Generyczne "Fail" — przyczynę ustalamy z porównania
                # rezystancji z limitem profilu.
                res_val = _safe_float(resistance)
                hi_limit = _safe_float(gnd_result.get("_hi_limit"))

                if (res_val is not None and hi_limit is not None
                        and res_val > hi_limit):
                    described = (
                        f"Rezystancja {res_val} mΩ > HI limit {hi_limit} mΩ"
                    )
                else:
                    described = "FAIL Ground Bond — sprawdź podłączenie PE"

            gnd_result["error_desc"] = described

            if ambiguous:
                gnd_result["error_desc"] += (
                    " [uwaga: kolejność pól prąd/rezystancja niepotwierdzona]"
                )
        else:
            gnd_result["status"] = "unknown"
            gnd_result["error_desc"] = f"Nierozpoznany status GND: {verdict!r}"

        return gnd_result

    # ── Przekaźnik ─────────────────────────────────────────────────────────
    def _open_relay(self) -> RelayController | None:
        if not self._relay_port:
            return None

        relay = RelayController(port=self._relay_port)
        relay.connect()
        return relay

    def _force_relay_home(self, relay: RelayController):
        """
        Wymusza pozycję HIPOT przed startem testu — dla KAŻDEGO profilu.

        Nieudany powrót na HIPOT po poprzednim teście zostawiał przekaźnik
        na PE, a profil bez GND w ogóle nie tworzył RelayController, czyli
        nikt tego nie korygował i wysokie napięcie szło torem PE.
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
    def _run_hipot_step(self, profile: dict, result: dict, heartbeat=None):
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

        self._assert_ready_for_test("HiPot")

        if not self._cmd("TEST", wait=0.5):
            raise HipotError(
                "TEST — brak ACK. Sprawdź Interlock / DUT / tryb lokalny."
            )

        slot, raw = self._poll_for_result(
            HIPOT_SLOTS,
            min_wait=ramp + dwell,
            margin=self.result_margin_s,
            label="HiPot",
            accept=_is_hipot_record,
            accept_fallback=_is_plausible_record,
            heartbeat=heartbeat,
        )

        self._parse_hipot_result(raw, result)

    # ── Krok 2: Ground Bond ────────────────────────────────────────────────
    def _run_gnd_step(self, gnd_profile: dict, gnd_result: dict,
                      relay: RelayController | None, heartbeat=None):
        gc = float(gnd_profile.get("current", 25.0))
        g_hi = float(gnd_profile.get("hi_limit", 100))
        g_lo = float(gnd_profile.get("lo_limit", 0))
        g_dwell = float(gnd_profile.get("dwell", 1.0))
        g_offset = gnd_profile.get("offset", 0)
        g_freq = gnd_profile.get("frequency", 1)

        gnd_result["_hi_limit"] = g_hi
        gnd_result["_lo_limit"] = g_lo

        log.info("─── KROK 2: Ground Bond ───")

        # Napięcie zdjęte i wynik HiPot odczytany — dopiero teraz styki.
        if self.relay_switch_delay_s > 0:
            log.info("Odczekanie %.1f s przed przełączeniem przekaźnika.",
                     self.relay_switch_delay_s)
            self._sleep_abortable(self.relay_switch_delay_s, heartbeat=heartbeat)

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

        self._assert_ready_for_test("Ground Bond")

        if not self._cmd("TEST", wait=0.5):
            raise HipotError("TEST (GND) — brak ACK. Sprawdź CURRENT OUTPUT.")

        slot, raw = self._poll_for_result(
            GND_SLOTS,
            min_wait=g_dwell,
            margin=self.gnd_result_margin_s,
            label="Ground Bond",
            accept=_is_gnd_record,
            accept_fallback=_is_plausible_record,
            heartbeat=heartbeat,
        )

        self._parse_gnd_result(raw, gnd_result, expected_current=gc)

    # ── GŁÓWNA METODA ──────────────────────────────────────────────────────
    def run_full_sequence(self, profile: dict, test_timeout: float | None = None) -> dict:
        """
        Sekwencja:
            1. HiPot AC (krok 1)
            2. Ground Bond (krok 2), tylko gdy profile["ground_bond"] != None
               i tylko gdy HiPot zwrócił jawne PASS.

        test_timeout przyjmowany dla zgodności wstecznej i traktowany jako
        MARGINES doliczany do ramp+dwell, nigdy jako skrócenie czekania.
        """
        if test_timeout is not None:
            self.result_margin_s = max(float(test_timeout), 5.0)

        gnd_profile = profile.get("ground_bond")
        has_gnd = gnd_profile is not None

        hipot_result = _empty_result()
        gnd_result = _empty_result() if has_gnd else None

        relay = None
        heartbeat = None

        try:
            # ── Przekaźnik: pozycja wyjściowa dla KAŻDEGO testu ────────────
            if self._relay_port:
                try:
                    relay = self._open_relay()
                    self._force_relay_home(relay)
                    # getattr, nie relay.ping — brak metody nie może wywrócić testu
                    heartbeat = getattr(relay, "ping", None)
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
                self._run_hipot_step(profile, hipot_result, heartbeat=heartbeat)

            except HipotAborted as e:
                hipot_result["aborted"] = True
                hipot_result["status"] = "aborted"
                hipot_result["error"] = str(e)
                self.emergency_stop()
                return {"hipot": hipot_result, "gnd": gnd_result}

            except (HipotError, RelayError) as e:
                hipot_result["error"] = str(e)
                return {"hipot": hipot_result, "gnd": gnd_result}

            if hipot_result.get("status") != "pass":
                log.warning("HiPot: status=%s — Ground Bond pominięty.",
                            hipot_result.get("status"))
                return {"hipot": hipot_result, "gnd": gnd_result}

            if not has_gnd:
                log.info("Profil bez Ground Bond — sekwencja zakończona.")
                return {"hipot": hipot_result, "gnd": None}

            # ── Krok 2 ────────────────────────────────────────────────────
            try:
                self._run_gnd_step(gnd_profile, gnd_result, relay,
                                   heartbeat=heartbeat)

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

            slot, raw = self._poll_for_result(
                HIPOT_SLOTS,
                min_wait=ramp + dwell,
                margin=self.result_margin_s,
                label="HiPot (fallback)",
                accept=_is_hipot_record,
                accept_fallback=_is_plausible_record,
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
    """
    Liczba z pola metrologicznego testera.

    Slaughter zwraca wartości poza zakresem jako '>20.0' albo '<1.00'
    (potwierdzone w logu ze stanowiska: current_ma = '>20.0'). Sam float()
    się na tym wywraca, dlatego obcinamy operator porównania — wartość
    graniczna jest wystarczająca do porównania z limitem.
    """
    if value is None:
        return None

    text = str(value).strip().replace(",", ".")

    if not text:
        return None

    if text[0] in "<>=~":
        text = text[1:].strip()

    # Odetnij ewentualne jednostki dokleiane przez niektóre firmware.
    for unit in ("mA", "kV", "mOhm", "mΩ", "MΩ", "A", "V", "s"):
        if text.endswith(unit):
            text = text[: -len(unit)].strip()
            break

    try:
        return float(text)
    except ValueError:
        return None
