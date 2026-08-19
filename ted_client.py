"""
ted_client.py
-------------
Klient TED / Azure SQL dla aplikacji HiPot Bose.

Wysyła XML DataWipeResultV2 do endpointu TED.

Dane od IT:
    Contract    = 10058
    Program     = BOSE_BYD
    MachineName = HIPOT
    TestArea    = BYDGOSZCZ

Tryby:
    db_type="TEST"  -> tabele testowe TED
    db_type=""      -> produkcja

Wymaga zmiennej środowiskowej albo pliku .env:
    TED_FUNCTION_KEY=<klucz funkcji>

Zmiany względem poprzedniej wersji:

  1. Klucz funkcji idzie w NAGŁÓWKU x-functions-key, a nie w query stringu.
     W URL-u lądował w logach proxy, logach Azure i historii połączeń.
     Awaryjny powrót do ?code=: zmienna środowiskowa TED_KEY_IN_QUERY=1.

  2. Klucz czytany przy KAŻDYM wywołaniu, nie raz przy imporcie modułu.
     Wcześniej .env pojawiający się po starcie procesu nie był widziany
     aż do restartu aplikacji.

  3. Werdykt globalny liczony przez wspólny moduł verdict — wcześniej
     ted_client i result_logger miały własne, rozjeżdżające się kopie
     logiki (pusty wynik GND: tutaj PASS, w CSV FAIL).

  4. Spool na dysk przy nieudanej wysyłce + flush_spool() przy kolejnym
     teście. Wcześniej nieudana wysyłka oznaczała, że rekord nie powstanie
     w TED nigdy — przy dłuższej awarii sieci ginęła cała zmiana.
"""

from __future__ import annotations

import os
import socket
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import verdict as V
from app_logging import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Próba załadowania .env
# ─────────────────────────────────────────────────────────────
def _load_env_file() -> None:
    """
    Ładuje .env LEŻĄCY OBOK EXE.

    Samo load_dotenv() szuka pliku względem katalogu roboczego albo ramki
    wywołania — w buildzie PyInstallera potrafi go nie znaleźć. Podajemy
    ścieżkę jawnie, a domyślne szukanie zostawiamy jako uzupełnienie.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Bez python-dotenv kod nadal działa — TED_FUNCTION_KEY musi być
        # wtedy zmienną środowiskową Windows.
        log.info("python-dotenv niedostępne — czytam tylko zmienne środowiskowe.")
        return

    from app_logging import app_dir

    env_path = app_dir() / ".env"

    if env_path.is_file():
        load_dotenv(env_path, override=False)
        log.info("Wczytano .env: %s", env_path)
    else:
        log.warning(".env nie znaleziony obok aplikacji (%s) — "
                    "TED_FUNCTION_KEY musi być zmienną środowiskową.", env_path)

    load_dotenv(override=False)


_load_env_file()


# ─────────────────────────────────────────────────────────────
# Stałe TED / BOSE
# ─────────────────────────────────────────────────────────────
TED_BASE_URL = "https://usengprod-functionapp.azurewebsites.net/api/DataWipeResult"

DEFAULT_CONTRACT = "10058"
DEFAULT_PROGRAM = "BOSE_BYD"
DEFAULT_MACHINE_NAME = "HIPOT"
DEFAULT_TEST_AREA = "BYDGOSZCZ"
DEFAULT_MISC_INFO = ""

SPOOL_DIRNAME = "ted_queue"


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _ted_now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _to_ted_datetime(value: datetime) -> str:
    """Zamienia datetime na format TED: 2026-07-14T13:31:07"""
    if value.tzinfo is not None:
        value = value.astimezone()

    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _limit(value, max_len: int) -> str:
    return _safe_text(value)[:max_len]


def _add(parent, tag: str, text=""):
    el = SubElement(parent, tag)
    el.text = _safe_text(text)
    return el


def _function_key() -> str:
    """
    Czyta klucz funkcji przy każdym wywołaniu.

    Świadomie nie trzymamy go w zmiennej modułu — .env albo zmienna
    środowiskowa mogą pojawić się już po starcie procesu.
    """
    return os.getenv("TED_FUNCTION_KEY", "").strip()


def _key_in_query() -> bool:
    return os.getenv("TED_KEY_IN_QUERY", "").strip().lower() in ("1", "true", "yes")


def _ted_request(xml_bytes: bytes) -> urllib.request.Request:
    """
    Buduje żądanie do TED.

    Klucz funkcji domyślnie w nagłówku x-functions-key. Nie trafia wtedy do
    URL-a, czyli nie ląduje w logach pośredników.
    """
    key = _function_key()

    if not key:
        raise ValueError(
            "Brak TED_FUNCTION_KEY. Sprawdź plik .env albo zmienne środowiskowe."
        )

    headers = {
        "Content-Type": "application/xml; charset=utf-8",
        "Accept": "application/xml, text/plain, */*",
    }

    if _key_in_query():
        url = f"{TED_BASE_URL}?code={key}"
    else:
        url = TED_BASE_URL
        headers["x-functions-key"] = key

    return urllib.request.Request(
        url, data=xml_bytes, method="POST", headers=headers
    )


def _validate_payload(payload: dict):
    """Minimalna walidacja przed wysyłką."""
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

    missing = [
        key for key in required_fields
        if not _safe_text(payload.get(key)).strip()
    ]

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
    """Buduje XML DataWipeResultV2 zgodnie z przykładem IT."""
    _validate_payload(payload)

    root = Element(
        "ns0:DataWipeResultV2",
        attrib={"xmlns:ns0": "http://winit/webservices/"},
    )

    xdoc = SubElement(root, "ns0:xDoc")
    record = SubElement(xdoc, "record")

    result = _safe_text(payload["result"]).upper()

    _add(record, "SerialNumber", _limit(payload["serial_number"], 100))
    _add(record, "PartNumber", _limit(payload.get("part_number", ""), 100))

    _add(record, "StartTime", payload["start_time"])
    _add(record, "EndTime", payload["end_time"])

    _add(record, "MachineName", _limit(payload["machine_name"], 100))
    _add(record, "Result", _limit(result, 100))
    _add(record, "TestArea", _limit(payload["test_area"], 100))
    _add(record, "CellNumber", _limit(payload.get("cell_number", ""), 100))
    _add(record, "Program", _limit(payload["program"], 100))
    _add(record, "MiscInfo", _limit(payload.get("misc_info", DEFAULT_MISC_INFO), 100))
    _add(record, "MACAddress", _limit(payload.get("mac_address", ""), 100))

    _add(record, "Msg", _limit(payload.get("msg", ""), 500))

    _add(record, "LogFile", payload.get("log_file", ""))
    _add(record, "Username", _limit(payload.get("username", ""), 200))
    _add(record, "OrderNumber", _limit(payload.get("order_number", ""), 100))
    _add(record, "UploadTime", payload.get("upload_time", ""))

    _add(record, "Contract", _limit(payload["contract"], 100))
    _add(record, "FileReference", _limit(payload.get("csv_path", ""), 200))
    _add(record, "FailureReference", _limit(payload.get("failure_reference", ""), 100))

    _add(
        record,
        "FailureNumber",
        _limit(
            payload.get("failure_number", "0000" if result == "PASS" else "9999"),
            2000,
        ),
    )

    _add(record, "ErrItem", _limit(payload.get("error_desc", ""), 2000))
    _add(record, "BatteryHealthGrade", _limit(payload.get("battery_health_grade", ""), 50))
    _add(record, "TestAreaOrig", _limit(payload.get("test_area_orig", ""), 100))

    for sub in payload.get("subtests", []):
        st = SubElement(record, "subtest")

        _add(st, "TestIDNumber", _limit(sub.get("id", ""), 100))
        _add(st, "TestName", _limit(sub.get("name", ""), 100))
        _add(st, "TestDesc", _limit(sub.get("desc", ""), 100))
        _add(st, "StartTime", sub.get("start_time", ""))
        _add(st, "EndTime", sub.get("end_time", ""))
        _add(st, "Result", _limit(_safe_text(sub.get("result", "")).upper(), 100))
        _add(st, "ErrorMessage", _limit(sub.get("error_message", ""), 100))
        _add(st, "ResultMessage", _limit(sub.get("result_message", ""), 8000))

    _add(root, "ns0:dbType", payload.get("db_type", "TEST"))
    _add(root, "ns0:servicename", "")
    _add(root, "ns0:accesstoken", "")

    return tostring(root, encoding="utf-8", xml_declaration=True)


# ─────────────────────────────────────────────────────────────
# Payload builder pod aplikację HiPot
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
    expects_gnd: bool = False,
    aborted: bool = False,
) -> dict:
    """
    Buduje payload TED na podstawie wyniku z HipotController.run_full_sequence().

    expects_gnd -> czy profil wymagał Ground Bond. Bez tego nie da się odróżnić
                   "profil bez GND" od "profil z GND, którego wynik przepadł";
                   ta druga sytuacja NIE może iść do TED jako PASS.
    """
    start_ted = _to_ted_datetime(start_time)
    end_ted = _to_ted_datetime(end_time)

    overall = V.compute_overall(hipot, gnd, expects_gnd=expects_gnd, aborted=aborted)
    global_result = V.to_ted_result(overall)

    hipot_v = V.step_verdict(hipot)
    gnd_executed = V.step_executed(gnd)
    gnd_v = V.step_verdict(gnd) if gnd_executed else None

    hipot_result = _safe_text(hipot.get("result")) if hipot else ""
    gnd_result = _safe_text(gnd.get("result")) if gnd else ""

    error_desc = (
        (hipot.get("error_desc") or hipot.get("error") if hipot else "")
        or ((gnd.get("error_desc") or gnd.get("error")) if gnd and gnd_executed else "")
        or ""
    )

    if overall == V.UNKNOWN and not error_desc:
        error_desc = "Brak jednoznacznego wyniku testu."

    # ── FailureNumber ─────────────────────────────────────────
    if overall == V.PASS:
        failure_number = "0000"
    elif aborted or overall == V.ABORTED:
        failure_number = "ABORTED"
    elif hipot_v != V.PASS:
        failure_number = _safe_text(
            (hipot.get("error_code") if hipot else "") or f"HIPOT_{hipot_v}"
        )
    elif gnd_executed and gnd_v != V.PASS:
        failure_number = f"GND_{gnd_v}"
    elif expects_gnd and not gnd_executed:
        # HiPot zdał, ale krok Ground Bond wymagany przez profil nie zostawił
        # żadnego wyniku. To niekompletny test, nie sztuka NOK — TED ma to
        # rozróżnić po FailureNumber.
        failure_number = "GND_MISSING"
    else:
        failure_number = "9999"

    subtests = [
        {
            "id": "1",
            "name": "HiPot ACW",
            "desc": profile_key,
            "start_time": start_ted,
            "end_time": end_ted,
            "result": V.to_binary(hipot_v),
            "error_message": (hipot.get("error_desc") or hipot.get("error") or "")
                             if hipot else "",
            "result_message": (
                f"Voltage={hipot.get('voltage', '') if hipot else ''}kV; "
                f"Current={hipot.get('current', '') if hipot else ''}mA; "
                f"Time={hipot.get('time', '') if hipot else ''}s; "
                f"Verdict={hipot_v}"
            ),
        }
    ]

    if gnd is not None and gnd_executed:
        subtests.append(
            {
                "id": "2",
                "name": "Ground Bond",
                "desc": profile_key,
                "start_time": start_ted,
                "end_time": end_ted,
                "result": V.to_binary(gnd_v),
                "error_message": gnd.get("error_desc") or gnd.get("error") or "",
                "result_message": (
                    f"Resistance={gnd.get('resistance', '')}mOhm; "
                    f"Current={gnd.get('current', '')}A; "
                    f"Time={gnd.get('time', '')}s; "
                    f"Verdict={gnd_v}"
                ),
            }
        )

    elif expects_gnd:
        # Profil wymagał Ground Bond, a kroku nie wykonano. Zapisujemy to jawnie,
        # żeby w TED nie wyglądało to na test wykonany w całości.
        subtests.append(
            {
                "id": "2",
                "name": "Ground Bond",
                "desc": profile_key,
                "start_time": start_ted,
                "end_time": end_ted,
                "result": "FAIL",
                "error_message": "Krok nie wykonany",
                "result_message": "Ground Bond wymagany przez profil, brak wyniku",
            }
        )

    msg = (
        f"Profile={profile_key}; "
        f"Overall={overall}; "
        f"HiPot={hipot_result or hipot_v}; "
        f"GND={gnd_result if gnd_executed else 'N/A'}; "
        f"PC={socket.gethostname()}"
    )

    return {
        "serial_number": sn,
        "part_number": part_number,
        "mac_address": "",
        "order_number": order_number,

        "contract": DEFAULT_CONTRACT,
        "start_time": start_ted,
        "end_time": end_ted,
        "machine_name": DEFAULT_MACHINE_NAME,
        "cell_number": cell_number,
        "program": DEFAULT_PROGRAM,
        "username": operator,

        "result": global_result,
        "overall_verdict": overall,
        "test_area": DEFAULT_TEST_AREA,

        "test_area_orig": "",
        "misc_info": DEFAULT_MISC_INFO,

        "msg": msg[:500],

        "failure_number": failure_number,
        "error_desc": error_desc,

        "csv_path": csv_path,
        "upload_time": "",

        "subtests": subtests,
    }


# ─────────────────────────────────────────────────────────────
# Spool — kolejka na dysku przy awarii sieci
# ─────────────────────────────────────────────────────────────
def _spool_dir(log_dir: str = "logs") -> Path:
    path = Path(log_dir) / SPOOL_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def spool_payload(xml_bytes: bytes, sn: str, log_dir: str = "logs") -> str | None:
    """Zapisuje nieudaną wysyłkę na dysk, żeby dało się ją ponowić później."""
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_sn = "".join(c for c in str(sn) if c.isalnum() or c in "-_")[:40]
        target = _spool_dir(log_dir) / f"{stamp}_{safe_sn or 'unknown'}.xml"

        target.write_bytes(xml_bytes)
        log.warning("TED: payload zakolejkowany na dysku: %s", target)

        return str(target)

    except OSError as e:
        log.error("TED: nie udało się zakolejkować payloadu: %s", e)
        return None


def flush_spool(log_dir: str = "logs", max_items: int = 25,
                timeout: int = 10) -> dict:
    """
    Próbuje wysłać zaległe payloady z kolejki.

    Wołane na starcie każdego testu — tanie, gdy kolejka jest pusta.
    Zwraca {"sent": n, "failed": n, "remaining": n}.
    """
    sent = failed = 0

    try:
        directory = _spool_dir(log_dir)
        pending = sorted(directory.glob("*.xml"))[:max_items]
    except OSError as e:
        log.error("TED: nie można odczytać kolejki: %s", e)
        return {"sent": 0, "failed": 0, "remaining": 0}

    if not pending:
        return {"sent": 0, "failed": 0, "remaining": 0}

    log.info("TED: w kolejce %d zaległych payloadów", len(pending))

    for item in pending:
        try:
            xml_bytes = item.read_bytes()
            response = _post(xml_bytes, timeout=timeout)

            if response.get("ok"):
                item.unlink(missing_ok=True)
                sent += 1
                log.info("TED: wysłano zaległy payload %s", item.name)
            else:
                failed += 1
                log.warning("TED: zaległy payload %s nadal nieudany: %s",
                            item.name, response.get("error"))
                break  # sieć dalej nie działa — nie ma sensu bić głową

        except OSError as e:
            failed += 1
            log.error("TED: błąd odczytu %s: %s", item, e)

    try:
        remaining = len(list(_spool_dir(log_dir).glob("*.xml")))
    except OSError:
        remaining = -1

    return {"sent": sent, "failed": failed, "remaining": remaining}


# ─────────────────────────────────────────────────────────────
# Wysyłka do TED
# ─────────────────────────────────────────────────────────────
def _post(xml_bytes: bytes, timeout: int = 15) -> dict:
    """Surowy POST bez budowania XML — używane też przy flushu kolejki."""
    try:
        req = _ted_request(xml_bytes)

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")

            log.info("TED POST -> HTTP %s | %s", status, body[:300])

            if status in (200, 201, 202):
                return {"ok": True, "status": status, "body": body[:1000]}

            return {
                "ok": False,
                "status": status,
                "error": f"HTTP {status}: {body[:1000]}",
            }

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "error": f"HTTP {e.code}: {body[:1000]}"}

    except urllib.error.URLError as e:
        return {"ok": False, "error": f"Brak sieci / TED niedostępny: {e.reason}"}

    except socket.timeout:
        return {"ok": False, "error": f"TED timeout (>{timeout}s)"}

    except ValueError as e:
        return {"ok": False, "error": f"Konfiguracja TED: {e}"}

    except Exception as e:
        log.exception("Nieoczekiwany błąd wysyłki TED")
        return {"ok": False, "error": f"Błąd TED: {e}"}


def send_to_ted(payload: dict, db_type: str = "TEST", timeout: int = 15,
                spool_on_failure: bool = True, log_dir: str = "logs") -> dict:
    """
    Wysyła payload do TED.

    Przy niepowodzeniu payload trafia do kolejki na dysku i zostanie ponowiony
    przy następnym teście (flush_spool). Zwracany słownik ma wtedy
    queued=True — result_logger zapisze to jako QUEUED, a nie jako utracone.
    """
    payload = dict(payload)
    payload["db_type"] = db_type

    try:
        xml_bytes = _build_xml(payload)

    except ValueError as e:
        # Błąd budowy payloadu — kolejkowanie nie ma sensu, XML jest wadliwy.
        log.error("TED: payload nieprawidłowy: %s", e)
        return {"ok": False, "skipped": False, "queued": False,
                "error": f"Payload TED nieprawidłowy: {e}"}

    result = _post(xml_bytes, timeout=timeout)
    result.setdefault("skipped", False)
    result.setdefault("queued", False)

    if not result.get("ok") and spool_on_failure:
        spool_path = spool_payload(
            xml_bytes, payload.get("serial_number", ""), log_dir=log_dir
        )
        if spool_path:
            result["queued"] = True
            result["spool_path"] = spool_path

    return result


# ─────────────────────────────────────────────────────────────
# Debug / podgląd XML
# ─────────────────────────────────────────────────────────────
def build_xml_preview(payload: dict, db_type: str = "TEST") -> str:
    """Zwraca XML jako tekst. Przydatne, gdy IT poprosi o przykład payloadu."""
    payload = dict(payload)
    payload["db_type"] = db_type

    return _build_xml(payload).decode("utf-8", errors="replace")
