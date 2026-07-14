"""
result_logger.py
----------------
Lokalny logger wyników HiPot / Ground Bond.

Zapisuje wyniki do dziennego pliku CSV w folderze logs.

Format CSV jest dostosowany pod polskiego Excela:
    - separator: ;
    - encoding: utf-8-sig
    - liczby dziesiętne: przecinek zamiast kropki
    - serial_number jako tekst, żeby Excel nie ucinał zer z przodu
"""

import csv
from datetime import datetime
from pathlib import Path


FIELDNAMES = [
    "timestamp",
    "serial_number",
    "operator",
    "profile",
    "result",
    "hipot_result",
    "voltage_kv",
    "current_ma",
    "test_time_s",
    "gnd_result",
    "gnd_resistance",
    "gnd_current",
    "gnd_time_s",
    "error_desc",
    "ted_sent",
    "ted_error",
]


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _excel_text(value) -> str:
    """
    Wymusza tekst w Excelu.

    Dzięki temu SN typu 050546 nie zostanie pokazany jako 50546.
    Excel wyświetli wartość bez cudzysłowów.
    """
    text = _safe_text(value)

    if not text:
        return ""

    text = text.replace('"', '""')
    return f'="{text}"'


def _parse_float(value):
    """
    Próbuje zamienić wartość na float.
    Obsługuje zarówno kropkę, jak i przecinek dziesiętny.
    """
    text = _safe_text(value)

    if not text or text in ("—", "-", "None"):
        return None

    text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def _fmt_decimal(value, digits: int = 2) -> str:
    """
    Formatuje liczbę pod polskiego Excela:
        1.5 -> 1,50
        25.0 -> 25,00
    """
    number = _parse_float(value)

    if number is None:
        return _safe_text(value)

    return f"{number:.{digits}f}".replace(".", ",")


def _is_pass(result_value) -> bool:
    return _safe_text(result_value).lower() == "pass"


def _ted_sent_value(ted_status: dict | None) -> str:
    if not ted_status:
        return "NO"

    if ted_status.get("skipped"):
        return "SKIPPED"

    if ted_status.get("ok"):
        return "YES"

    return "NO"


def _ted_error_value(ted_status: dict | None) -> str:
    if not ted_status:
        return ""

    if ted_status.get("skipped"):
        return ""

    if ted_status.get("ok"):
        return ""

    return _safe_text(ted_status.get("error", ""))


def _backup_old_csv_format_if_needed(log_file: Path):
    """
    Jeżeli istnieje już dzisiejszy CSV w starym formacie z przecinkami,
    nie dopisujemy do niego nowych wierszy z separatorem średnikowym.

    Stary plik zostanie przemianowany na *_old_comma_format_HHMMSS.csv.
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
        f"{log_file.stem}_old_comma_format_"
        f"{datetime.now().strftime('%H%M%S')}"
        f"{log_file.suffix}"
    )
    backup_path = log_file.with_name(backup_name)

    try:
        log_file.rename(backup_path)
        print(f"Stary CSV przeniesiony do: {backup_path}")
    except OSError as e:
        print(f"Nie udało się przenieść starego CSV: {e}")


def save_result(
    log_dir: str,
    sn: str,
    operator: str,
    profile_name: str,
    hipot: dict,
    gnd: dict | None,
    ted_status: dict | None = None,
) -> str:
    """
    Zapisuje pojedynczy wynik testu do dziennego CSV.

    Zwraca ścieżkę do pliku CSV.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_path / f"hipot_log_{today}.csv"

    _backup_old_csv_format_if_needed(log_file)

    write_header = not log_file.exists() or log_file.stat().st_size == 0

    hipot_result = _safe_text(hipot.get("result"))
    gnd_result = _safe_text(gnd.get("result")) if gnd else ""

    hipot_pass = _is_pass(hipot_result)
    gnd_pass = True if gnd is None else _is_pass(gnd_result)

    overall_result = "PASS" if hipot_pass and gnd_pass else "FAIL"

    error_desc = (
        _safe_text(hipot.get("error_desc"))
        or _safe_text(hipot.get("error"))
        or (_safe_text(gnd.get("error_desc")) if gnd else "")
        or (_safe_text(gnd.get("error")) if gnd else "")
    )

    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        # SN jako tekst, żeby Excel nie usuwał zer z przodu.
        "serial_number": _excel_text(sn),

        "operator": _safe_text(operator),
        "profile": _safe_text(profile_name),
        "result": overall_result,

        "hipot_result": hipot_result,
        "voltage_kv": _fmt_decimal(hipot.get("voltage"), 2),
        "current_ma": _fmt_decimal(hipot.get("current"), 2),
        "test_time_s": _fmt_decimal(hipot.get("time"), 1),

        "gnd_result": gnd_result,
        "gnd_resistance": _fmt_decimal(gnd.get("resistance"), 2) if gnd else "",
        "gnd_current": _fmt_decimal(gnd.get("current"), 2) if gnd else "",
        "gnd_time_s": _fmt_decimal(gnd.get("time"), 1) if gnd else "",

        "error_desc": error_desc,

        # Przy local-only będzie SKIPPED zamiast NO z błędem.
        "ted_sent": _ted_sent_value(ted_status),
        "ted_error": _ted_error_value(ted_status),
    }

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

    return str(log_file)