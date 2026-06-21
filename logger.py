import os
from datetime import datetime

LOGS_DIR = "logs"

def save_result(sn: str, hrid: str, operator_name: str, profile_key: str,
                profile: dict, result: dict):
    os.makedirs(LOGS_DIR, exist_ok=True)

    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M%S")
    filename = f"{sn}_{timestamp}.txt"
    filepath = os.path.join(LOGS_DIR, filename)

    verdict = result.get("result", "UNKNOWN")
    voltage = result.get("voltage", "—")
    current = result.get("current", "—")
    test_time = result.get("time", "—")
    error = result.get("error", "")

    lines = [
        f"Date:            {now.strftime('%Y-%m-%d')}",
        f"Time:            {now.strftime('%H:%M:%S')}",
        f"Serial Number:   {sn}",
        f"Profile:         {profile_key}",
        f"Test Type:       {profile.get('type', 'ACW')}",
        f"Voltage:         {profile.get('voltage', '—')} kV",
        f"HI Limit:        {profile.get('hi_limit', '—')} mA",
        f"LO Limit:        {profile.get('lo_limit', '—')} mA",
        f"Ramp:            {profile.get('ramp', '—')} s",
        f"Dwell:           {profile.get('dwell', '—')} s",
        f"",
        f"Measured Voltage:{voltage} kV",
        f"Measured Current:{current} mA",
        f"Test Time:       {test_time} s",
        f"",
        f"Result:          {verdict}",
        f"Error:           {error if error else '—'}",
        f"",
        f"Operator HRID:   {hrid}",
        f"Operator Name:   {operator_name}",
    ]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath