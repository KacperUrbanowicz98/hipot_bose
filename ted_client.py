import socket
import urllib.request
import urllib.error
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring


TED_ENDPOINT = (
    "https://usengprod-functionapp.azurewebsites.net/api/"
    "DataWipeResult?code=[USUNIETO]"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_xml(payload: dict) -> bytes:
    root = Element("ns0:DataWipeResultV2", attrib={
        "xmlns:ns0": "http://schemas.datacontract.org/2004/07/DataWipeResult"
    })

    def add(parent, tag, text=""):
        el = SubElement(parent, tag)
        el.text = str(text) if text is not None else ""
        return el

    # Routing
    add(root, "ns0:dbType",      payload.get("db_type", "TEST"))
    add(root, "ns0:servicename", "")
    add(root, "ns0:accesstoken", "")

    # Device
    add(root, "SerialNumber", payload["serial_number"])
    add(root, "PartNumber",   payload.get("part_number", ""))
    add(root, "MACAddress",   "")
    add(root, "OrderNumber",  payload.get("order_number", ""))
    add(root, "Contract",     payload["contract"])

    # Metadata
    add(root, "StartTime",   payload["start_time"])
    add(root, "EndTime",     payload["end_time"])
    add(root, "UploadTime",  _utc_now())
    add(root, "MachineName", payload["machine_name"])
    add(root, "CellNumber",  payload.get("cell_number", ""))
    add(root, "Program",     payload["program"])
    add(root, "Username",    payload.get("username", ""))

    # Results
    add(root, "Result",           payload["result"])
    add(root, "TestArea",         payload.get("test_area", ""))
    add(root, "TestAreaOrig",     payload.get("test_area", ""))
    add(root, "Msg",              payload.get("msg", ""))
    add(root, "MiscInfo",         payload.get("misc_info", "HIPOT"))
    add(root, "FailureReference", "")
    add(root, "FailureNumber",    payload.get("failure_number", "0000"))
    add(root, "ErrItem",          payload.get("error_desc", ""))
    add(root, "LogFile",          "")
    add(root, "LogFileStatus",    "")
    add(root, "FileReference",    payload.get("csv_path", ""))

    # Subtesty
    for sub in payload.get("subtests", []):
        st = SubElement(root, "subtest")
        add(st, "TestIDNumber",  sub.get("id", ""))
        add(st, "TestName",      sub.get("name", ""))
        add(st, "TestDesc",      sub.get("desc", ""))
        add(st, "StartTime",     sub.get("start_time", ""))
        add(st, "EndTime",       sub.get("end_time", ""))
        add(st, "Result",        sub.get("result", ""))
        add(st, "ErrorMessage",  sub.get("error_message", ""))
        add(st, "ResultMessage", sub.get("result_message", ""))

    return tostring(root, encoding="utf-8", xml_declaration=True)


def send_to_ted(payload: dict, db_type: str = "TEST") -> dict:
    """
    Wysyła wynik do TED Azure SQL.
    db_type="TEST" → tabele testowe | db_type="" → produkcja
    Zwraca {"ok": True} lub {"ok": False, "error": str}
    """
    payload = dict(payload)
    payload["db_type"] = db_type
    try:
        xml_bytes = _build_xml(payload)
        req = urllib.request.Request(
            TED_ENDPOINT,
            data=xml_bytes,
            method="POST",
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.getcode()
            body   = resp.read().decode("utf-8", errors="replace")
            print(f"TED POST → HTTP {status} | {body[:200]}")
            if status in (200, 201, 202):
                return {"ok": True, "status": status}
            return {"ok": False, "error": f"HTTP {status}: {body[:300]}"}

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {body[:200]}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"Brak sieci / TED niedostępny: {e.reason}"}
    except socket.timeout:
        return {"ok": False, "error": "TED timeout (>15s) — wynik tylko lokalnie"}
    except Exception as e:
        return {"ok": False, "error": f"Błąd TED: {e}"}