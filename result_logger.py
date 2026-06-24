import csv
import os
from datetime import datetime, timezone
from pathlib import Path


CSV_COLUMNS = [
    "timestamp", "serial_number", "operator", "profile",
    "result",
    "hipot_result", "voltage_kv", "current_ma", "test_time_s",
    "gnd_result", "gnd_resistance", "gnd_current", "gnd_time_s",
    "error_desc",
    "ted_sent", "ted_error",
]


def _csv_path(log_dir: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(log_dir, f"hipot_log_{today}.csv")


def save_result(
    log_dir:      str,
    sn:           str,
    operator:     str,
    profile_name: str,
    hipot:        dict,
    gnd:          dict | None,
    ted_status:   dict,
) -> str:
    """
    Dopisuje wiersz do dziennego CSV.
    Zwraca pełną ścieżkę do pliku CSV.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    path = _csv_path(log_dir)

    hipot_ok = hipot.get("result") == "Pass"
    gnd_ok   = (gnd is None) or (gnd.get("result") == "Pass")
    global_result = "PASS" if (hipot_ok and gnd_ok) else "FAIL"

    # Opis błędu — hipot ma priorytet, potem gnd
    error_desc = (
        hipot.get("error_desc") or hipot.get("error") or
        (gnd.get("error_desc") or gnd.get("error") if gnd else "") or ""
    )

    row = {
        "timestamp":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "serial_number":  sn,
        "operator":       operator,
        "profile":        profile_name,
        "result":         global_result,
        "hipot_result":   hipot.get("result", ""),
        "voltage_kv":     hipot.get("voltage", ""),
        "current_ma":     hipot.get("current", ""),
        "test_time_s":    hipot.get("time",    ""),
        "gnd_result":     gnd.get("result",     "") if gnd else "",
        "gnd_resistance": gnd.get("resistance", "") if gnd else "",
        "gnd_current":    gnd.get("current",    "") if gnd else "",
        "gnd_time_s":     gnd.get("time",       "") if gnd else "",
        "error_desc":     error_desc,
        "ted_sent":       "YES" if ted_status.get("ok") else "NO",
        "ted_error":      ted_status.get("error", ""),
    }

    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    return path