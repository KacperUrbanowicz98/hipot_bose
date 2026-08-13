"""
result_logger.py
----------------
Lokalny logger wyników HiPot / Ground Bond.

Zapisuje wyniki do dziennego pliku CSV w folderze logs.

Format CSV pod polskiego Excela:
    - separator: ;
    - encoding: utf-8-sig
    - liczby dziesiętne: przecinek zamiast kropki
    - serial_number jako tekst, żeby Excel nie ucinał zer z przodu

Zmiany:
  - werdykt liczony przez wspólny moduł verdict, a nie własną kopią logiki,
  - nowa kolumna overall_verdict (PASS/FAIL/ERROR/UNKNOWN/ABORTED) obok
    binarnej kolumny result (PASS/FAIL) — kolumna result zachowuje stary
    kontrakt dla istniejących raportów,
  - save_result() RZUCA wyjątek przy błędzie zapisu, zamiast pozwalać, żeby
    wywołujący połknął go w print(). Brak zapisu wyniku musi być widoczny
    dla operatora.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import verdict as V
from app_logging import get_logger

log = get_logger(__name__)


FIELDNAMES = [
    "timestamp",
    "serial_number",
    "operator",
    "profile",
    "result",            # PASS/FAIL — stary kontrakt, fail-safe
    "overall_verdict",   # PASS/FAIL/ERROR/UNKNOWN/ABORTED — pełna informacja
    "hipot_result",
    "voltage_kv",
    "current_ma",
    "test_time_s",
    "gnd_expected",
    "gnd_result",
    "gnd_resistance",
    "gnd_current",
    "gnd_time_s",
    "error_desc",
    "ted_sent",
    "ted_error",
]


class ResultLogError(Exception):
    """Nie udało się zapisać wyniku testu."""
    pass


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _excel_text(value) -> str:
    """Wymusza tekst w Excelu, żeby SN typu 050546 nie stracił zer wiodących."""
    text = _safe_text(value)

    if not text:
        return ""

    text = text.replace('"', '""')
    return f'="{text}"'


def _parse_float(value):
    text = _safe_text(value)

    if not text or text in ("—", "-", "None"):
        return None

    text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def _fmt_decimal(value, digits: int = 2) -> str:
    """Formatuje liczbę pod polskiego Excela: 1.5 -> 1,50"""
    number = _parse_float(value)

    if number is None:
        return _safe_text(value)

    return f"{number:.{digits}f}".replace(".", ",")


def _ted_sent_value(ted_status: dict | None) -> str:
    if not ted_status:
        return "NO"

    if ted_status.get("skipped"):
        return "SKIPPED"

    if ted_status.get("queued"):
        return "QUEUED"

    if ted_status.get("ok"):
        return "YES"

    return "NO"


def _ted_error_value(ted_status: dict | None) -> str:
    if not ted_status:
        return ""

    if ted_status.get("skipped") or ted_status.get("ok"):
        return ""

    return _safe_text(ted_status.get("error", ""))


def _backup_old_csv_format_if_needed(log_file: Path):
    """
    Jeżeli dzisiejszy CSV ma inny nagłówek (stary format), nie dopisujemy do
    niego nowych wierszy — stary plik jest przemianowany.
    """
    if not log_file.exists() or log_file.stat().st_size == 0:
        return

    try:
        with log_file.open("r", encoding="utf-8-sig", errors="ignore") as f:
            first_line = f.readline().strip()
    except OSError:
        return

    expected_header = ";".join(FIELDNAMES)

    if first_line == expected_header:
        return

    backup_name = (
        f"{log_file.stem}_old_format_"
        f"{datetime.now().strftime('%H%M%S')}"
        f"{log_file.suffix}"
    )
    backup_path = log_file.with_name(backup_name)

    try:
        log_file.rename(backup_path)
        log.info("Stary CSV przeniesiony do: %s", backup_path)
    except OSError as e:
        log.error("Nie udało się przenieść starego CSV: %s", e)


def save_result(
    log_dir: str,
    sn: str,
    operator: str,
    profile_name: str,
    hipot: dict,
    gnd: dict | None,
    ted_status: dict | None = None,
    expects_gnd: bool = False,
    aborted: bool = False,
) -> str:
    """
    Zapisuje pojedynczy wynik testu do dziennego CSV.

    expects_gnd -> czy profil wymagał Ground Bond. Bez tego nie da się odróżnić
                   "profil bez GND" od "profil z GND, którego wynik przepadł".

    Zwraca ścieżkę do pliku CSV.
    Rzuca ResultLogError, gdy zapis się nie powiedzie.
    """
    overall = V.compute_overall(hipot, gnd, expects_gnd=expects_gnd, aborted=aborted)

    log_path = Path(log_dir)

    try:
        log_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ResultLogError(f"Nie można utworzyć katalogu logów {log_path}: {e}")

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_path / f"hipot_log_{today}.csv"

    _backup_old_csv_format_if_needed(log_file)

    write_header = not log_file.exists() or log_file.stat().st_size == 0

    hipot_result = _safe_text(hipot.get("result")) if hipot else ""
    gnd_result = _safe_text(gnd.get("result")) if gnd else ""

    error_desc = (
        _safe_text(hipot.get("error_desc")) if hipot else ""
    ) or (
        _safe_text(hipot.get("error")) if hipot else ""
    ) or (
        _safe_text(gnd.get("error_desc")) if gnd else ""
    ) or (
        _safe_text(gnd.get("error")) if gnd else ""
    )

    if overall == V.UNKNOWN and not error_desc:
        error_desc = "Brak jednoznacznego wyniku — sztuki nie wolno zwolnić."

    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "serial_number": _excel_text(sn),
        "operator": _safe_text(operator),
        "profile": _safe_text(profile_name),

        # Binarnie, fail-safe: wszystko poza jawnym PASS to FAIL.
        "result": V.to_binary(overall),
        "overall_verdict": overall,

        "hipot_result": hipot_result,
        "voltage_kv": _fmt_decimal(hipot.get("voltage"), 2) if hipot else "",
        "current_ma": _fmt_decimal(hipot.get("current"), 2) if hipot else "",
        "test_time_s": _fmt_decimal(hipot.get("time"), 1) if hipot else "",

        "gnd_expected": "YES" if expects_gnd else "NO",
        "gnd_result": gnd_result,
        "gnd_resistance": _fmt_decimal(gnd.get("resistance"), 2) if gnd else "",
        "gnd_current": _fmt_decimal(gnd.get("current"), 2) if gnd else "",
        "gnd_time_s": _fmt_decimal(gnd.get("time"), 1) if gnd else "",

        "error_desc": error_desc,

        "ted_sent": _ted_sent_value(ted_status),
        "ted_error": _ted_error_value(ted_status),
    }

    try:
        with log_file.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=FIELDNAMES,
                delimiter=";",
                lineterminator="\n",
            )

            if write_header:
                writer.writeheader()

            writer.writerow(row)
            f.flush()

    except OSError as e:
        # Typowe przyczyny: plik otwarty w Excelu, brak uprawnień do folderu
        # obok EXE, pełny dysk. Wywołujący MUSI to pokazać operatorowi.
        raise ResultLogError(
            f"Nie można zapisać wyniku do {log_file}: {e}. "
            "Sprawdź, czy plik nie jest otwarty w Excelu i czy jest miejsce na dysku."
        )

    log.info("Wynik zapisany: %s | SN=%s | %s", log_file, sn, overall)

    return str(log_file)
