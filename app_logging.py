"""
app_logging.py
--------------
Logowanie do pliku zamiast print().

Powód: create_exe.py buduje aplikację z --windowed, więc EXE nie ma konsoli.
Wszystkie print() z kodu — łącznie z jedynym ostrzeżeniem o nieskonfigurowanym
przekaźniku i o błędzie zapisu CSV — szły donikąd. W części konfiguracji
PyInstaller/Windows zapis do nieistniejącego stdout potrafi dodatkowo rzucić
wyjątkiem, co samo w sobie było źródłem awarii.

Dwa strumienie:

    logs/app.log            -> diagnostyka aplikacji, rotowana
    logs/config_audit.log   -> ślad zmian konfiguracji, append-only

Użycie:
    from app_logging import get_logger
    log = get_logger(__name__)
    log.info("...")
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


APP_LOG_NAME = "app.log"
AUDIT_LOG_NAME = "config_audit.log"

_configured = False
_audit_logger: logging.Logger | None = None


# ── Ochrona przed brakiem stdout w buildzie --windowed ─────────────────────
class _NullStream:
    """Zastępnik sys.stdout/sys.stderr, gdy ich nie ma (PyInstaller windowed)."""

    def write(self, _data):
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False


def _ensure_std_streams():
    """
    W buildzie --windowed sys.stdout i sys.stderr bywają None.
    Każdy print() z kodu aplikacji albo z biblioteki trzeciej rzuca wtedy
    AttributeError. Podstawiamy zaślepkę, żeby to nie wywracało testu.
    """
    if getattr(sys, "stdout", None) is None:
        sys.stdout = _NullStream()

    if getattr(sys, "stderr", None) is None:
        sys.stderr = _NullStream()


# ── Ścieżki ────────────────────────────────────────────────────────────────
def app_dir() -> Path:
    """Katalog aplikacji — obok EXE po zbudowaniu, obok źródeł w developmencie."""
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent


def default_log_dir() -> Path:
    return app_dir() / "logs"


# ── Konfiguracja ───────────────────────────────────────────────────────────
def setup_logging(log_dir: str | os.PathLike | None = None,
                  level: int = logging.INFO) -> logging.Logger:
    """
    Konfiguruje logowanie aplikacji. Wołać raz, na starcie main.py.

    Bezpieczne przy wielokrotnym wywołaniu.
    """
    global _configured

    _ensure_std_streams()

    if _configured:
        return logging.getLogger("hipot")

    target = Path(log_dir) if log_dir else default_log_dir()

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Brak prawa zapisu w katalogu aplikacji — logujemy tylko na konsolę.
        target = None

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if target is not None:
        try:
            file_handler = RotatingFileHandler(
                target / APP_LOG_NAME,
                maxBytes=5 * 1024 * 1024,
                backupCount=10,
                encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            file_handler.setLevel(level)
            root.addHandler(file_handler)
        except OSError:
            pass

    # Konsola tylko wtedy, gdy realnie istnieje (build --console, development).
    if getattr(sys.stdout, "isatty", lambda: False)():
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        console.setLevel(level)
        root.addHandler(console)

    _configured = True

    log = logging.getLogger("hipot")
    log.info("=" * 60)
    log.info("Start aplikacji | frozen=%s | katalog=%s",
             bool(getattr(sys, "frozen", False)), app_dir())

    return log


def get_logger(name: str) -> logging.Logger:
    """Logger dla modułu. Nie wymaga wcześniejszego setup_logging()."""
    if not _configured:
        # Awaryjnie: nie gubimy komunikatów, jeśli ktoś zawoła logger przed setupem.
        _ensure_std_streams()

    short = name.rsplit(".", 1)[-1]
    return logging.getLogger(f"hipot.{short}")


# ── Audyt konfiguracji ─────────────────────────────────────────────────────
def get_audit_logger() -> logging.Logger:
    """
    Osobny, append-only log zmian konfiguracji.

    Trzymany oddzielnie od app.log, żeby rotacja diagnostyki nie kasowała
    śladu audytowego.
    """
    global _audit_logger

    if _audit_logger is not None:
        return _audit_logger

    logger = logging.getLogger("hipot_audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = default_log_dir()

    try:
        target.mkdir(parents=True, exist_ok=True)

        handler = RotatingFileHandler(
            target / AUDIT_LOG_NAME,
            maxBytes=10 * 1024 * 1024,
            backupCount=50,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)

    except OSError:
        pass

    _audit_logger = logger
    return logger


def audit(actor: str, action: str, details: str = "") -> None:
    """
    Zapisuje zdarzenie audytowe.

    actor   -> HRID + nazwisko osoby, która wykonała zmianę
    action  -> np. PROFILE_SAVE, PROFILE_DELETE, USER_ADD, PASSWORD_CHANGE
    details -> co konkretnie się zmieniło
    """
    try:
        get_audit_logger().info(
            "%s | %s | %s",
            actor or "UNKNOWN",
            action,
            details.replace("\n", " ") if details else "",
        )
    except Exception:
        # Audyt nigdy nie może wywrócić aplikacji.
        pass


def read_audit_tail(lines: int = 200) -> str:
    """Zwraca ostatnie N linii logu audytowego — do podglądu w panelu."""
    path = default_log_dir() / AUDIT_LOG_NAME

    if not path.exists():
        return "(brak wpisów audytowych)"

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            content = f.readlines()
        return "".join(content[-lines:])
    except OSError as e:
        return f"(nie można odczytać logu audytowego: {e})"


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
