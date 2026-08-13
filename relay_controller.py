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

Zmiany względem poprzedniej wersji:
  - print() zastąpione logowaniem do logs/app.log (build --windowed nie ma
    konsoli, więc cała diagnostyka przekaźnika wcześniej przepadała),
  - safe_return_to_hipot() raportuje niepowodzenie przez flagę
    last_return_failed, żeby warstwa wyżej mogła zareagować zamiast
    polegać na komunikacie, którego nikt nie widzi.

Uwaga:
  Jeżeli ESP zawiesi się całkowicie sprzętowo, kod może spróbować reconnectu
  i opcjonalnie resetu przez DTR/RTS, ale najpewniejszym rozwiązaniem jest
  watchdog po stronie firmware + stabilne zasilanie przekaźnika.
"""

from __future__ import annotations

import threading
import time

import serial
import serial.tools.list_ports

from app_logging import get_logger

log = get_logger(__name__)


class RelayError(Exception):
    """Wyjątek dla błędów komunikacji z przekaźnikiem ESP."""
    pass


class RelayController:
    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: int = 3,
        retries: int = 3,
        allow_dtr_reset: bool = False,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.allow_dtr_reset = allow_dtr_reset

        self._serial = None
        self._lock = threading.RLock()

        #: True, jeżeli ostatni safe_return_to_hipot() się nie powiódł.
        self.last_return_failed = False

    # ── Połączenie ─────────────────────────────────────────────────────────
    def connect(self):
        """Otwiera port COM bez resetowania ESP."""
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
                write_timeout=self.timeout,
                dsrdtr=False,
                rtscts=False,
            )

            # Nie resetuj ESP po otwarciu portu.
            self._serial.dtr = False
            self._serial.rts = False

            time.sleep(0.25)
            self._clear_buffers()
            self._wait_for_ready(timeout=1.0)

            log.info("Połączono z ESP: %s @ %s", self.port, self.baudrate)

        except serial.SerialException as e:
            raise RelayError(f"Błąd otwarcia portu ESP {self.port}: {e}")

    def disconnect(self):
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception as e:
            log.warning("Błąd zamykania portu ESP: %s", e)
        finally:
            self._serial = None

    def reconnect(self):
        """Miękki reconnect ESP."""
        log.info("RELAY: reconnect ESP/COM...")

        try:
            self.disconnect()
        except Exception as e:
            log.warning("RELAY: disconnect podczas reconnect: %s", e)

        time.sleep(0.8)
        self.connect()
        time.sleep(0.5)
        self._clear_buffers()

        log.info("RELAY: reconnect done")

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Niskopoziomowa obsługa serial ──────────────────────────────────────
    def _clear_buffers(self):
        if not self.is_connected:
            return

        try:
            self._serial.reset_input_buffer()
        except Exception:
            pass

        try:
            self._serial.reset_output_buffer()
        except Exception:
            pass

    def _wait_for_ready(self, timeout: float = 1.0):
        """Czeka krótko na READY z ESP. Brak READY nie zatrzymuje aplikacji."""
        if not self.is_connected:
            return

        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                line = self._serial.read_until(b"\n", size=128)
            except Exception:
                break

            decoded = line.decode("ascii", errors="replace").strip()

            if decoded:
                log.debug("ESP boot: %r", decoded)

            if "READY" in decoded:
                return

        log.debug("RelayController: brak READY w boot — kontynuuję")

    def _read_response(
        self,
        expected_prefixes: tuple[str, ...] = (),
        timeout: float = 2.0,
    ) -> str:
        """Czyta odpowiedź z ESP, pomijając linie poboczne (READY/STATE/WATCHDOG)."""
        deadline = time.time() + timeout
        last_line = ""

        while time.time() < deadline:
            raw = self._serial.read_until(b"\n", size=256)
            line = raw.decode("ascii", errors="replace").strip()

            if not line:
                continue

            last_line = line
            log.debug("RELAY RESP LINE << %r", line)

            if not expected_prefixes:
                return line

            if line.startswith(expected_prefixes):
                return line

            if (line == "READY"
                    or line.startswith("STATE:")
                    or line.startswith("WATCHDOG:")):
                continue

            continue

        return last_line

    def _send_cmd(
        self,
        cmd: str,
        wait: float = 0.3,
        expected_prefixes: tuple[str, ...] = (),
        read_timeout: float | None = None,
    ) -> str:
        if not self.is_connected:
            raise RelayError("Brak połączenia z ESP — port zamknięty.")

        if read_timeout is None:
            read_timeout = max(1.0, self.timeout)

        try:
            with self._lock:
                self._clear_buffers()

                payload = (cmd.strip() + "\n").encode("ascii")
                self._serial.write(payload)
                self._serial.flush()

                time.sleep(wait)

                resp = self._read_response(
                    expected_prefixes=expected_prefixes,
                    timeout=read_timeout,
                )

                log.debug("RELAY >> %-15r | RESP << %r", cmd, resp)
                return resp

        except serial.SerialTimeoutException as e:
            raise RelayError(f"Timeout zapisu do ESP podczas '{cmd}': {e}")

        except serial.SerialException as e:
            raise RelayError(f"Utrata połączenia z ESP podczas '{cmd}': {e}")

        except Exception as e:
            raise RelayError(f"Błąd komunikacji ESP podczas '{cmd}': {e}")

    # ── Reset / healthcheck ESP ────────────────────────────────────────────
    def reset_via_dtr(self):
        """Próba resetu ESP przez DTR/RTS. Domyślnie wyłączona."""
        if not self.allow_dtr_reset:
            log.debug("RELAY: reset_via_dtr pominięty — allow_dtr_reset=False")
            return

        if not self.is_connected:
            return

        try:
            log.info("RELAY: próba resetu ESP przez DTR/RTS")

            self._serial.dtr = True
            self._serial.rts = True
            time.sleep(0.25)

            self._serial.dtr = False
            self._serial.rts = False
            time.sleep(1.8)

            self._clear_buffers()
            self._wait_for_ready(timeout=2.0)

        except Exception as e:
            log.warning("RELAY: reset_via_dtr nieudany: %s", e)

    def ping(self) -> bool:
        try:
            resp = self._send_cmd(
                "PING",
                wait=0.2,
                expected_prefixes=("PONG",),
                read_timeout=1.5,
            )
            return resp == "PONG"

        except RelayError as e:
            log.warning("RELAY: PING failed: %s", e)
            return False

    def ping_or_reconnect(self) -> bool:
        if self.ping():
            return True

        log.warning("RELAY: brak PONG — próbuję reconnect ESP")

        try:
            self.reconnect()
        except Exception as e:
            log.error("RELAY: reconnect failed: %s", e)
            return False

        return self.ping()

    def watchdog_off(self):
        self._send_cmd(
            "WATCHDOG:OFF",
            wait=0.3,
            expected_prefixes=("OK:WATCHDOG_OFF",),
            read_timeout=1.5,
        )

    # ── Status ─────────────────────────────────────────────────────────────
    def get_status(self) -> str:
        """Zwraca PE, HIPOT albo UNKNOWN."""
        try:
            resp = self._send_cmd(
                "STATUS?",
                wait=0.25,
                expected_prefixes=("STATE:",),
                read_timeout=1.5,
            )

            if resp.startswith("STATE:"):
                state = resp.split(":", 1)[1].strip().upper()
                if state in ("PE", "HIPOT"):
                    return state

            return "UNKNOWN"

        except RelayError as e:
            log.warning("RELAY: get_status failed: %s", e)
            return "UNKNOWN"

    def is_hipot(self) -> bool:
        return self.get_status() == "HIPOT"

    def _confirm_state(self, expected: str, samples: int = 2,
                       delay: float = 0.20) -> bool:
        """Potwierdza stan kilka razy pod rząd."""
        expected = expected.upper()
        ok_count = 0

        for _ in range(samples):
            state = self.get_status()
            log.debug("RELAY STATE CHECK: expected=%r, got=%r", expected, state)

            if state == expected:
                ok_count += 1

            time.sleep(delay)

        return ok_count == samples

    # ── Przełączanie ───────────────────────────────────────────────────────
    def _switch(self, target: str) -> bool:
        """
        Wspólna logika przełączania z retry/reconnect/rescue.

        target: "PE" albo "HIPOT"
        """
        ok_prefix = f"OK:{target}"
        last_resp = ""
        last_state = "UNKNOWN"

        for attempt in range(1, self.retries + 1):
            log.info("RELAY %s attempt %d/%d", target, attempt, self.retries)

            try:
                self.ping_or_reconnect()
                time.sleep(0.20)

                resp = self._send_cmd(
                    target,
                    wait=0.90,
                    expected_prefixes=(ok_prefix, "ERR:RELAY_FAIL"),
                    read_timeout=2.0,
                )

                last_resp = resp

                time.sleep(0.45)
                last_state = self.get_status()

                if resp == ok_prefix and last_state == target:
                    if self._confirm_state(target, samples=2, delay=0.20):
                        log.info("RELAY: %s confirmed", target)
                        return True

                log.warning(
                    "RELAY WARN: %s not confirmed attempt=%d, resp=%r, state=%r",
                    target, attempt, resp, last_state,
                )

                if resp.startswith("ERR:RELAY_FAIL"):
                    time.sleep(0.8)

                if attempt == 2 and attempt < self.retries:
                    log.warning("RELAY: %s failed twice — reconnect ESP", target)
                    self.reconnect()

                time.sleep(0.8)

            except RelayError as e:
                log.warning("RELAY WARN: %s attempt %d/%d error: %s",
                            target, attempt, self.retries, e)

                if attempt == 2 and attempt < self.retries:
                    try:
                        self.reconnect()
                    except Exception as re:
                        log.error("RELAY WARN: reconnect after %s error failed: %s",
                                  target, re)

                time.sleep(0.8)

        # Ostatnia próba ratunkowa
        try:
            log.warning("RELAY: final rescue before %s", target)
            self.reset_via_dtr()

            self.ping_or_reconnect()
            time.sleep(0.25)

            resp = self._send_cmd(
                target,
                wait=1.0,
                expected_prefixes=(ok_prefix, "ERR:RELAY_FAIL"),
                read_timeout=2.5,
            )

            last_resp = resp

            time.sleep(0.5)
            last_state = self.get_status()

            if resp == ok_prefix and last_state == target:
                if self._confirm_state(target, samples=2, delay=0.20):
                    log.info("RELAY: %s confirmed after rescue", target)
                    return True

        except Exception as e:
            log.error("RELAY: final rescue %s failed: %s", target, e)

        raise RelayError(
            f"Przekaźnik nie przełączył się na {target} po retry/reconnect/reset — "
            f"ostatnia odpowiedź ESP: {last_resp!r}, ostatni status: {last_state!r}. "
            "Sprawdź CHECK_PIN D3, zasilanie ESP/przekaźnika, masę, przewody "
            "oraz zakłócenia od HiPot."
        )

    def set_pe(self) -> bool:
        """Przełącza przekaźnik na PE / Ground Bond."""
        return self._switch("PE")

    def set_hipot(self) -> bool:
        """Przełącza przekaźnik na HIPOT (pozycja domyślna/bezpieczna)."""
        result = self._switch("HIPOT")
        self.last_return_failed = False
        return result

    def safe_return_to_hipot(self) -> bool:
        """
        Awaryjny powrót do HIPOT — do użycia w bloku finally.

        Nie rzuca wyjątku dalej, ale ustawia last_return_failed=True, żeby
        warstwa wyżej mogła to zauważyć. Sam log nie wystarcza: przekaźnik
        zostawiony na PE kieruje wysokie napięcie w tor PE przy następnym
        teście, dlatego HipotController wymusza pozycję HIPOT także przed
        każdym kolejnym testem.
        """
        try:
            if self.is_connected:
                self.set_hipot()
                self.last_return_failed = False
                return True

            self.last_return_failed = True
            log.error("safe_return_to_hipot: brak połączenia z ESP — "
                      "pozycja przekaźnika NIEZNANA.")
            return False

        except RelayError as e:
            self.last_return_failed = True
            log.error("safe_return_to_hipot WARN: %s", e)
            return False

        except Exception as e:
            self.last_return_failed = True
            log.error("safe_return_to_hipot ERROR: %s", e)
            return False
