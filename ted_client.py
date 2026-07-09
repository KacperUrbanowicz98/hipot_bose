"""
ted_client.py
-------------
Klient TED / Azure SQL dla aplikacji HiPot.

Wysyła XML DataWipeResultV2 do endpointu TED.

Dane od IT:
    Contract    = 10058
    Program     = BOSE_BYD
    MachineName = HIPOT

Tryby:
    db_type="TEST"  -> tabele testowe TED
    db_type=""      -> produkcja

Wymaga pliku .env:
    TED_FUNCTION_KEY=twoj_klucz_funkcji
"""

import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring


# ─────────────────────────────────────────────────────────────
# Próba załadowania .env
# ─────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # Jeśli python-dotenv nie jest zainstalowane, kod nadal działa,
    # ale TED_FUNCTION_KEY musi być ustawione jako zmienna środowiskowa Windows.
    pass


# ─────────────────────────────────────────────────────────────
# Stałe TED / BOSE
# ─────────────────────────────────────────────────────────────
TED_BASE_URL = "https://usengprod-functionapp.azurewebsites.net/api/DataWipeResult"

TED_FUNCTION_KEY = os.getenv("TED_FUNCTION_KEY", "").strip()

DEFAULT_CONTRACT = "10058"
DEFAULT_PROGRAM = "BOSE_BYD"
DEFAULT_MACHINE_NAME = "HIPOT"
DEFAULT_TEST_AREA = "10058"
DEFAULT_MISC_INFO = "HIPOT"


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _utc_now() -> str:
    """Zwraca aktualny czas UTC w formacie wymaganym przez TED."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_utc_iso(value: datetime) -> str:
    """
    Zamienia datetime na UTC ISO 8601:
        2026-07-06T09:15:30Z
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_text(value) -> str:
    """Bezpiecznie zamienia None/liczby/inne wartości na tekst do XML."""
    if value is None:
        return ""
    return str(value)


def _add(parent, tag: str, text=""):
    """Dodaje element XML."""
    el = SubElement(parent, tag)
    el.text = _safe_text(text)
    return el


def _ted_endpoint() -> str:
    """
    Buduje endpoint TED z kluczem z .env.

    Nie trzymamy klucza w kodzie.
    """
    if not TED_FUNCTION_KEY:
        raise ValueError(
            "Brak TED_FUNCTION_KEY. Sprawdź plik .env albo zmienne środowiskowe."
        )

    return f"{TED_BASE_URL}?code={TED_FUNCTION_KEY}"


def _validate_payload(payload: dict):
    """
    Minimalna walidacja przed wysyłką do TED.
    Dzięki temu szybciej widzisz, czego brakuje.
    """
    required_fields = [
        "serial_number",
        "contract",
        "start_time",
        "end_time",
        "machine_name",
        "program",
        "result",
        "test_area",
    ]

    missing = []
    for key in required_fields:
        if not _safe_text(payload.get(key)).strip():
            missing.append(key)

    if missing:
        raise ValueError(f"Brak wymaganych pól TED: {', '.join(missing)}")

    result = _safe_text(payload.get("result")).upper()
    if result not in ("PASS", "FAIL"):
        raise ValueError(
            f"Nieprawidłowy Result dla TED: {result!r}. Dozwolone: PASS albo FAIL."
        )


# ─────────────────────────────────────────────────────────────
# XML builder
# ─────────────────────────────────────────────────────────────
def _build_xml(payload: dict) -> bytes:
    """
    Buduje XML DataWipeResultV2 dla TED.

    Ważne:
    API TED wymaga elementu <record>.
    Pola głównego testu muszą być wewnątrz <record>,
    a nie bezpośrednio pod DataWipeResultV2.
    """
    _validate_payload(payload)

    root = Element(
        "ns0:DataWipeResultV2",
        attrib={
            "xmlns:ns0": "http://schemas.datacontract.org/2004/07/DataWipeResult"
        },
    )

    # ─────────────────────────────────────────────────────────
    # Routing TED
    # ─────────────────────────────────────────────────────────
    # TEST -> tabele testowe
    # pusty string -> produkcja
    _add(root, "ns0:dbType", payload.get("db_type", "TEST"))
    _add(root, "ns0:servicename", "")
    _add(root, "ns0:accesstoken", "")

    # ─────────────────────────────────────────────────────────
    # Główny rekord testu
    # ─────────────────────────────────────────────────────────
    record = SubElement(root, "record")

    # Device / identyfikacja
    _add(record, "SerialNumber", payload["serial_number"])
    _add(record, "PartNumber", payload.get("part_number", ""))
    _add(record, "MACAddress", payload.get("mac_address", ""))
    _add(record, "OrderNumber", payload.get("order_number", ""))
    _add(record, "Contract", payload["contract"])

    # Metadata wykonania testu
    _add(record, "StartTime", payload["start_time"])
    _add(record, "EndTime", payload["end_time"])
    _add(record, "UploadTime", payload.get("upload_time", _utc_now()))
    _add(record, "MachineName", payload["machine_name"])
    _add(record, "CellNumber", payload.get("cell_number", ""))
    _add(record, "Program", payload["program"])
    _add(record, "Username", payload.get("username", ""))

    # Wynik główny
    result = _safe_text(payload["result"]).upper()

    _add(record, "Result", result)
    _add(record, "TestArea", payload["test_area"])
    _add(record, "TestAreaOrig", payload.get("test_area_orig", payload["test_area"]))
    _add(record, "Msg", payload.get("msg", ""))
    _add(record, "MiscInfo", payload.get("misc_info", DEFAULT_MISC_INFO))

    _add(record, "FailureReference", payload.get("failure_reference", ""))
    _add(
        record,
        "FailureNumber",
        payload.get("failure_number", "0000" if result == "PASS" else "9999"),
    )
    _add(record, "ErrItem", payload.get("error_desc", ""))

    # Pliki / referencje
    # Na razie nie robimy uploadu CSV do Blob Storage.
    # FileReference to tylko tekstowa referencja do lokalnego pliku/logu.
    _add(record, "LogFile", payload.get("log_file", ""))
    _add(record, "LogFileStatus", payload.get("log_file_status", ""))
    _add(record, "FileReference", payload.get("csv_path", ""))

    # ─────────────────────────────────────────────────────────
    # Subtesty: HIPOT / GROUND_BOND
    # ─────────────────────────────────────────────────────────
    # Jeśli API będzie marudzić na strukturę subtestów, na test można
    # tymczasowo ustawić payload["subtests"] = [] w test_ted_send.py.
    for sub in payload.get("subtests", []):
        st = SubElement(root, "subtest")
        _add(st, "TestIDNumber", sub.get("id", ""))
        _add(st, "TestName", sub.get("name", ""))
        _add(st, "TestDesc", sub.get("desc", ""))
        _add(st, "StartTime", sub.get("start_time", ""))
        _add(st, "EndTime", sub.get("end_time", ""))
        _add(st, "Result", _safe_text(sub.get("result", "")).upper())
        _add(st, "ErrorMessage", sub.get("error_message", ""))
        _add(st, "ResultMessage", sub.get("result_message", ""))

    return tostring(root, encoding="utf-8", xml_declaration=True)


# ─────────────────────────────────────────────────────────────
# Payload builder pod Twoją aplikację HiPot
# ─────────────────────────────────────────────────────────────
def build_hipot_payload(
    sn: str,
    operator: str,
    profile_key: str,
    hipot: dict,
    gnd: dict | None,
    start_time: datetime,
    end_time: datetime,
    csv_path: str = "",
    part_number: str = "",
    order_number: str = "",
    cell_number: str = "",
) -> dict:
    """
    Buduje payload TED na podstawie wyniku z HipotController.run_full_sequence().

    Parametry:
        sn          -> numer seryjny DUT
        operator    -> np. "12101333 Kacper Urbanowicz"
        profile_key -> np. "3KV", "2_5KV", "1_5KV_GND"
        hipot       -> słownik wyniku HiPot
        gnd         -> słownik wyniku Ground Bond albo None
        start_time  -> czas startu testu
        end_time    -> czas końca testu
        csv_path    -> ścieżka do lokalnego CSV/logu, opcjonalnie
    """
    start_iso = _to_utc_iso(start_time)
    end_iso = _to_utc_iso(end_time)

    hipot_result = _safe_text(hipot.get("result"))
    gnd_result = _safe_text(gnd.get("result")) if gnd else ""

    hipot_ok = hipot_result == "Pass"
    gnd_ok = gnd is None or gnd_result == "Pass"

    global_result = "PASS" if hipot_ok and gnd_ok else "FAIL"

    error_desc = (
        hipot.get("error_desc")
        or hipot.get("error")
        or ((gnd.get("error_desc") or gnd.get("error")) if gnd else "")
        or ""
    )

    failure_number = "0000"
    if global_result == "FAIL":
        if not hipot_ok:
            failure_number = _safe_text(hipot.get("error_code") or "HIPOT_FAIL")
        elif gnd and not gnd_ok:
            failure_number = "GND_FAIL"
        else:
            failure_number = "9999"

    subtests = [
        {
            "id": "HIPOT",
            "name": "HiPot ACW",
            "desc": profile_key,
            "start_time": start_iso,
            "end_time": end_iso,
            "result": "PASS" if hipot_ok else "FAIL",
            "error_message": hipot.get("error_desc") or hipot.get("error") or "",
            "result_message": (
                f"Voltage={hipot.get('voltage', '')}kV; "
                f"Current={hipot.get('current', '')}mA; "
                f"Time={hipot.get('time', '')}s"
            ),
        }
    ]

    if gnd is not None:
        subtests.append(
            {
                "id": "GROUND_BOND",
                "name": "Ground Bond",
                "desc": profile_key,
                "start_time": start_iso,
                "end_time": end_iso,
                "result": "PASS" if gnd_ok else "FAIL",
                "error_message": gnd.get("error_desc") or gnd.get("error") or "",
                "result_message": (
                    f"Resistance={gnd.get('resistance', '')}mOhm; "
                    f"Current={gnd.get('current', '')}A; "
                    f"Time={gnd.get('time', '')}s"
                ),
            }
        )

    return {
        "serial_number": sn,
        "part_number": part_number,
        "mac_address": "",
        "order_number": order_number,
        "contract": DEFAULT_CONTRACT,
        "start_time": start_iso,
        "end_time": end_iso,
        "machine_name": DEFAULT_MACHINE_NAME,
        "cell_number": cell_number,
        "program": DEFAULT_PROGRAM,
        "username": operator,
        "result": global_result,
        "test_area": DEFAULT_TEST_AREA,
        "test_area_orig": DEFAULT_TEST_AREA,
        "msg": (
            f"Profile={profile_key}; "
            f"HiPot={hipot_result}; "
            f"GND={gnd_result if gnd else 'N/A'}; "
            f"PC={socket.gethostname()}"
        ),
        "misc_info": DEFAULT_MISC_INFO,
        "failure_number": failure_number,
        "error_desc": error_desc,
        "csv_path": csv_path,
        "subtests": subtests,
    }


# ─────────────────────────────────────────────────────────────
# Wysyłka do TED
# ─────────────────────────────────────────────────────────────
def send_to_ted(payload: dict, db_type: str = "TEST", timeout: int = 15) -> dict:
    """
    Wysyła payload do TED.

    db_type:
        "TEST" -> tabele testowe
        ""     -> produkcja

    Zwraca:
        {"ok": True, "status": 200, "body": "..."}
    albo:
        {"ok": False, "error": "..."}
    """
    payload = dict(payload)
    payload["db_type"] = db_type

    try:
        xml_bytes = _build_xml(payload)

        req = urllib.request.Request(
            _ted_endpoint(),
            data=xml_bytes,
            method="POST",
            headers={
                "Content-Type": "application/xml; charset=utf-8",
                "Accept": "application/xml, text/plain, */*",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")

            print(f"TED POST -> HTTP {status} | {body[:500]}")

            if status in (200, 201, 202):
                return {
                    "ok": True,
                    "status": status,
                    "body": body[:1000],
                }

            return {
                "ok": False,
                "status": status,
                "error": f"HTTP {status}: {body[:1000]}",
            }

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": e.code,
            "error": f"HTTP {e.code}: {body[:1000]}",
        }

    except urllib.error.URLError as e:
        return {
            "ok": False,
            "error": f"Brak sieci / TED niedostępny: {e.reason}",
        }

    except socket.timeout:
        return {
            "ok": False,
            "error": f"TED timeout (>{timeout}s) — wynik tylko lokalnie",
        }

    except ValueError as e:
        return {
            "ok": False,
            "error": f"Payload TED nieprawidłowy: {e}",
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"Błąd TED: {e}",
        }


# ─────────────────────────────────────────────────────────────
# Debug / podgląd XML
# ─────────────────────────────────────────────────────────────
def build_xml_preview(payload: dict, db_type: str = "TEST") -> str:
    """
    Zwraca XML jako tekst.
    Przydatne, gdy IT poprosi o przykład payloadu.
    """
    payload = dict(payload)
    payload["db_type"] = db_type
    return _build_xml(payload).decode("utf-8", errors="replace")