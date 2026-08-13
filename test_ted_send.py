"""
test_ted_send.py
----------------
Ręczny test wysyłki TED / Azure dla HiPot Bose.

Ten skrypt:
    - nie łączy się z HiPotem,
    - nie zapisuje lokalnego CSV jako realnego testu,
    - wysyła sztuczne rekordy do TED TEST,
    - generuje XML preview do wysłania IT.

Wysyła:
    TESTTED000020 -> PASS
    TESTTED000021 -> FAIL

Wymaga:
    - poprawionego ted_client.py zgodnego z przykładem IT,
    - pliku .env z TED_FUNCTION_KEY,
    - python-dotenv, jeśli korzystasz z .env:
        pip install python-dotenv
"""

from datetime import datetime, timezone

from ted_client import build_hipot_payload, build_xml_preview, send_to_ted


DB_TYPE = "TEST"


def send_case(
    sn: str,
    profile_key: str,
    hipot_result: dict,
    gnd_result: dict | None = None,
):
    start = datetime.now(timezone.utc)
    end = datetime.now(timezone.utc)

    payload = build_hipot_payload(
        sn=sn,
        operator="12101333 Kacper Urbanowicz",
        profile_key=profile_key,
        hipot=hipot_result,
        gnd=gnd_result,
        start_time=start,
        end_time=end,
        csv_path="manual_test_ted_send.csv",
    )

    print("\n" + "=" * 80)
    print(f"TED TEST CASE: {sn}")
    print("=" * 80)

    print("\n========== XML PREVIEW ==========\n")
    xml_preview = build_xml_preview(payload, db_type=DB_TYPE)
    print(xml_preview)

    print("\n========== SEND TO TED TEST ==========\n")
    result = send_to_ted(payload, db_type=DB_TYPE)

    print(result)

    if result.get("ok"):
        print(f"\n✅ TED TEST OK — Azure przyjął payload dla SN {sn}.")
    else:
        print(f"\n❌ TED TEST FAIL — SN {sn}. Sprawdź error powyżej.")

    return result


def main():
    # ------------------------------------------------------------------
    # CASE 1: PASS + HiPot subtest
    # ------------------------------------------------------------------
    pass_hipot_result = {
        "result": "Pass",
        "status": "pass",
        "voltage": "3.00",
        "current": "1.23",
        "time": "2.0",
        "error_desc": "",
    }

    send_case(
        sn="TESTTED000020",
        profile_key="3KV",
        hipot_result=pass_hipot_result,
        gnd_result=None,
    )

    # ------------------------------------------------------------------
    # CASE 2: FAIL + HiPot subtest
    # ------------------------------------------------------------------
    fail_hipot_result = {
        "result": "Fail",
        "status": "fail",
        "voltage": "3.00",
        "current": "5.20",
        "time": "2.0",
        "error_code": "0001",
        "error_desc": "Testowy FAIL - HI limit przekroczony",
    }

    send_case(
        sn="TESTTED000021",
        profile_key="3KV",
        hipot_result=fail_hipot_result,
        gnd_result=None,
    )

    print("\n" + "=" * 80)
    print("GOTOWE")
    print("=" * 80)
    print("Poproś IT o sprawdzenie:")
    print("  - TESTTED000020 -> PASS")
    print("  - TESTTED000021 -> FAIL")
    print("  - czy pojawiły się SubTests/SubTestLogs")
    print("  - czy MainTest/DataWipeResult nadal wygląda poprawnie")


if __name__ == "__main__":
    main()