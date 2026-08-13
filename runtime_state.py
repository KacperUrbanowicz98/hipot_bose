"""
runtime_state.py
----------------
Stan współdzielony między ekranem operatora a panelem inżynieryjnym.

Powód: panel inżynieryjny otwierał własne połączenie z testerem i z ESP
(przyciski "→ PE", "→ HIPOT", "Test połączenia (RESET)") bez sprawdzenia,
czy w tle nie trwa test. grab_set() blokuje tylko interakcję z oknem, nie
wątek roboczy. Efektem mogło być przełączenie styków przekaźnika pod
napięciem albo RESET w połowie sekwencji.

Ten moduł daje jedną flagę, którą widzą oba okna.
"""

from __future__ import annotations

import threading


_test_running = threading.Event()
_lock = threading.Lock()
_current_sn = ""


def set_test_running(running: bool, sn: str = "") -> None:
    """Ustawia/zdejmuje flagę 'trwa test'. Wołane wyłącznie z main_screen."""
    global _current_sn

    with _lock:
        _current_sn = sn if running else ""

    if running:
        _test_running.set()
    else:
        _test_running.clear()


def test_in_progress() -> bool:
    """True, jeśli w tle trwa sekwencja testowa."""
    return _test_running.is_set()


def current_sn() -> str:
    with _lock:
        return _current_sn


def guard_message() -> str:
    """Komunikat do pokazania, gdy akcja jest zablokowana przez trwający test."""
    sn = current_sn()
    suffix = f" (SN: {sn})" if sn else ""
    return (
        f"⛔ Trwa test{suffix} — akcja zablokowana.\n"
        "Przełączanie przekaźnika lub komendy do testera w trakcie testu "
        "grożą przełączeniem styków pod napięciem."
    )
