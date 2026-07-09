from datetime import datetime, timezone

from ted_client import build_hipot_payload, send_to_ted, build_xml_preview


def main():
    start = datetime.now(timezone.utc)

    # Sztuczny wynik HiPot PASS
    hipot_result = {
        "result": "Fail",
        "status": "fail",
        "voltage": "3.00",
        "current": "5.20",
        "time": "2.0",
        "error_code": "0001",
        "error_desc": "Testowy FAIL — HI limit przekroczony",
    }

    # Bez Ground Bond na pierwszy test
    gnd_result = None

    payload = build_hipot_payload(
        sn="TESTTED000002",
        operator="12101333 Kacper Urbanowicz",
        profile_key="3KV",
        hipot=hipot_result,
        gnd=gnd_result,
        start_time=start,
        end_time=datetime.now(timezone.utc),
        csv_path="manual_test_ted_send.csv",
    )

    print("\n========== XML PREVIEW ==========\n")
    print(build_xml_preview(payload, db_type="TEST"))

    print("\n========== SEND TO TED TEST ==========\n")
    result = send_to_ted(payload, db_type="TEST")

    print(result)

    if result.get("ok"):
        print("\n✅ TED TEST OK — Azure przyjął payload.")
    else:
        print("\n❌ TED TEST FAIL — sprawdź error powyżej.")


if __name__ == "__main__":
    main()