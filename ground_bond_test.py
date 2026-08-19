"""
ground_bond_test.py
-------------------
Izolowany skrypt diagnostyczny Ground Bond dla Slaughter 4320.
NIE jest pakowany do EXE.

═══════════════════════════════════════════════════════════════════════════
POPRAWIONE 2026-08-18 — poprzednia wersja miała BŁĘDNY opis formatu wyniku
═══════════════════════════════════════════════════════════════════════════

Stary komentarz w tym pliku twierdził:

    GND,<step>,<Pass|Fail>,<resistance_mΩ>,<current_A>,<time_s>
                            ^^^^^^^^^^ rezystancja przed prądem  <- BŁĄD

i parser czytał `resistance = parts[3]`, `current = parts[4]`.

To było odwrotnie i przez pół roku wprowadzało w błąd. Prawidłowa kolejność
jest potwierdzona dwoma niezależnymi źródłami:

  1. Manual, "Failure Mode Displays" (str. 19) — wyświetlacz przy błędzie GND:
         1-1 HI-Lmt   1.0s
         30.0A GND  150mΩ        <- PRĄD przed rezystancją
     a `RD <step>?` zwraca {memory-step, test type, status, meter1, meter2, meter3}

  2. Log produkcyjny 2026-08-17: przy zadanych 25,0 A pierwsze pole miało
     wartość 24,90 (czyli prąd), drugie 73 / 95 / 106 (rezystancja).

Prawidłowy format:
    <step>,GND,<status>,<current_A>,<resistance_mΩ>,<time_s>

Druga poprawka: tester NIE zwraca słowa "Fail". Zwraca opisowy status —
`Pass`, `HI-Limit`, `LO-Limit`, `OFL`. Stary parser porównywał `== "Pass"`
i wszystko inne wrzucał do jednego worka bez wyjaśnienia przyczyny.

Testuje sekwencję:
  RESET → SPR 1 → FL 1 → SS 2 → SAG → EC → EH → EL → EDW → EO → EF
        → TEST → RD 2? / RD 1?

Użycie:
  python ground_bond_test.py
  python ground_bond_test.py --port COM12
  python ground_bond_test.py --port COM12 --skip-sag
  python ground_bond_test.py --current 25.0 --hi-limit 100
  python ground_bond_test.py --zero    (pomiar toru bez DUT — patrz niżej)

POMIAR TORU (--zero):
  Zwiera się kable pomiarowe BEZ DUT i mierzy rezystancję własną toru:
  kabel prądowy + styki przekaźnika + oprzyrządowanie. To ta wartość powinna
  trafić do nastawy Offset, jeśli proces na to pozwala. Bez tego wchodzi ona
  do każdego wyniku — na stanowisku odczyty 95-106 mΩ przy limicie 100 mΩ.
"""

import argparse
import time

import serial
import serial.tools.list_ports


# ── Parametry testu ────────────────────────────────────────────────────────
PORT = "COM11"
BAUDRATE = 9600
TIMEOUT = 3

GND_PROFILE = {
    "current":   25.0,   # A   — zakres testera 3.0 - 30.0
    "hi_limit":  100,    # mΩ  — max 510 (3-10 A) / 200 (10.1-25 A) / 150 (25.1-30 A)
    "lo_limit":  0,      # mΩ
    "dwell":     1.0,    # s
    "offset":    0,      # mΩ  — 0-100
    "frequency": 1,      # 1=60Hz, 0=50Hz
}

STEP = 2   # krok Ground Bond w Memory 1

# ── Statusy zwracane przez tester (manual s. 19) ───────────────────────────
PASS_TOKENS = ("pass", "passed")

FAIL_DESCRIPTIONS = {
    "hi-limit": "rezystancja powyżej HI limit albo poza zakresem pomiarowym",
    "hi-lmt":   "rezystancja powyżej HI limit albo poza zakresem pomiarowym",
    "lo-limit": "rezystancja poniżej LO limit",
    "lo-lmt":   "rezystancja poniżej LO limit",
    "ofl":      "rezystancja poza zakresem pomiarowym",
    "abort":    "test przerwany na testerze (RESET / Interlock)",
}

#: Górna granica zakresu pomiarowego rezystancji wg prądu (manual s. 19).
METER_RANGES = ((10.0, 510.0), (25.0, 200.0), (30.0, 150.0))


def meter_range(current):
    for max_current, max_resistance in METER_RANGES:
        if current <= max_current:
            return max_resistance
    return None


def to_float(value):
    """Pola metrologiczne mogą mieć postać '>20.0' albo '<1.00'."""
    text = str(value or "").strip().replace(",", ".")
    if text and text[0] in "<>=~":
        text = text[1:].strip()
    try:
        return float(text)
    except ValueError:
        return None


# ── Helpers ────────────────────────────────────────────────────────────────
def send(ser, cmd, wait=0.6, label=None):
    """Wysyła komendę, czeka, zwraca surowe bajty."""
    ser.reset_input_buffer()
    ser.write((cmd.strip() + "\r\n").encode("ascii"))
    time.sleep(wait)
    resp = ser.read_all()
    ack = ("✔ ACK" if b"\x06" in resp
           else ("✘ NAK" if b"\x15" in resp else "— brak odpowiedzi"))
    print(f"  {(label or cmd):<22} → {ack}   raw={resp!r}")
    return resp


def query_raw(ser, cmd, wait=1.0):
    """Wysyła zapytanie, zwraca (surowe bajty, string)."""
    ser.reset_input_buffer()
    ser.write((cmd.strip() + "\r\n").encode("ascii"))
    time.sleep(wait)
    raw_bytes = ser.read_all()
    raw_str = raw_bytes.decode("ascii", errors="replace").strip()
    print(f"  {cmd:<22} → raw={raw_bytes!r}  decoded={raw_str!r}")
    return raw_bytes, raw_str


def cmd_ok(ser, command, wait=0.6, label=None):
    return b"\x06" in send(ser, command, wait, label)


def divider(title=""):
    print(f"\n{'─' * 56}")
    if title:
        print(f"  {title}")
    print(f"{'─' * 56}")


# ── Fazy testu ─────────────────────────────────────────────────────────────
def phase_connection(ser):
    divider("FAZA 1: Sprawdzenie połączenia")
    raw_bytes, raw = query_raw(ser, "SA?", wait=0.6)

    if len(raw_bytes) == 0:
        print("  ⚠ Brak odpowiedzi na SA? — sprawdź port i kabel RS-232")
        return False

    print(f"  ✔ Tester odpowiada: {raw!r}")
    print("  → Tę odpowiedź warto wpisać do config.json:")
    print("      hipot.status_idle_tokens = [\"...\"]")
    return True


def phase_setup(ser, profile, skip_sag=False):
    divider(f"FAZA 2: Konfiguracja Ground Bond (krok {STEP})")

    allowed = meter_range(profile["current"])
    if allowed and profile["hi_limit"] > allowed:
        print(f"  ✘ HI limit {profile['hi_limit']} mΩ przekracza zakres "
              f"{allowed:.0f} mΩ dla prądu {profile['current']} A")
        print("    Tester odrzuci komendę EH. Zmień limit albo prąd.")
        return False

    steps = [("RESET", 0.4), ("SPR 1", 0.6), ("FL 1", 0.3), (f"SS {STEP}", 0.3)]

    if not skip_sag:
        steps.append(("SAG", 0.3))

    steps += [
        (f"EC {profile['current']:.2f}", 0.3),
        (f"EH {profile['hi_limit']}",    0.3),
        (f"EL {profile['lo_limit']}",    0.3),
        (f"EDW {profile['dwell']:.1f}",  0.3),
        (f"EO {profile['offset']}",      0.3),
        (f"EF {profile['frequency']}",   0.3),
    ]

    for cmd, wait in steps:
        if cmd == "RESET":
            send(ser, cmd, wait)      # RESET nie zwraca ACK
            continue
        if not cmd_ok(ser, cmd, wait):
            print(f"\n  ✘ PROBLEM: '{cmd}' nie dostał ACK — zatrzymuję")
            return False

    print("\n  ✔ Wszystkie parametry zaakceptowane")
    return True


def phase_verify_params(ser):
    divider(f"FAZA 3: Weryfikacja wgranych parametrów (LS {STEP}?)")
    raw_bytes, raw = query_raw(ser, f"LS {STEP}?", wait=1.0)

    if raw:
        print(f"  Parametry kroku {STEP}: {raw}")
    else:
        print("  ⚠ LS? nie odpowiedział — komenda może nie być obsługiwana")


def phase_test(ser, profile):
    divider("FAZA 4: Uruchomienie TEST")

    if not cmd_ok(ser, "TEST", wait=0.5):
        print("  ✘ TEST — brak ACK. Możliwe przyczyny:")
        print("    • Interlock aktywny (wtyczka interlockowa z tyłu testera)")
        print("    • Tester nie jest w trybie Remote (SPR 1 nie zadziałał)")
        print("    • Zatrzaśnięty poprzedni błąd — wymaga RESET na testerze")
        return False

    wait_time = profile["dwell"] + 1.5
    print(f"  Test w toku... minimum {wait_time:.1f} s")

    steps = 20
    for i in range(steps + 1):
        bar = "█" * i + "░" * (steps - i)
        print(f"  [{bar}] {i * 5}%", end="\r")
        time.sleep(wait_time / steps)
    print(f"  [{'█' * steps}] 100%")
    return True


def phase_result(ser, profile):
    """
    Odczyt wyniku z OBU slotów.

    Slaughter 4320 odkłada wynik GND raz pod RD 2?, raz pod RD 1? — zależnie
    od tego, jak został wysłany TEST. Odpytujemy oba i raportujemy, w którym
    wynik faktycznie był. To jest informacja, którą warto zanotować.
    """
    divider("FAZA 5: Odczyt wyniku")

    print("  [pre-check] SA? po teście:")
    query_raw(ser, "SA?", wait=0.6)
    print()

    for slot in (f"RD {STEP}?", "RD 1?"):
        raw_bytes, raw = query_raw(ser, slot, wait=1.0)

        if b"\x15" in raw_bytes and len(raw_bytes) <= 4:
            print(f"  {slot} → NAK, próbuję następny slot\n")
            continue

        parts = [p.strip() for p in raw.split(",")]

        if len(parts) < 5:
            print(f"  {slot} → format nierozpoznany: {raw!r}\n")
            continue

        if any(p.upper() in ("ACW", "DCW", "IR") for p in parts[:3]):
            print(f"  {slot} → to wynik HiPot, nie Ground Bond\n")
            continue

        print(f"\n  ✔✔ WYNIK GROUND BOND ZNALEZIONY W: {slot}")
        print(f"      ^ zanotuj to — potwierdza, którego slotu używa tester\n")
        _parse_gnd_result(raw, profile)
        return

    print("\n  ⚠ Żaden slot nie zwrócił wyniku Ground Bond.")
    print("    • test mógł się nie wykonać (Interlock / brak DUT)")
    print("    • po poprzedniej awarii tester wymaga RESET na panelu")


def _parse_gnd_result(raw, profile):
    """
    Parser wyniku Ground Bond.

    Format: <step>,GND,<status>,<current_A>,<resistance_mΩ>,<time_s>
    Pola liczone WZGLĘDEM pola statusu, więc obsłużone są też warianty
    bez znacznika GND i z dodatkowym numerem pliku z przodu.
    """
    parts = [p.strip() for p in raw.split(",")]
    print(f"  Pola ({len(parts)}): {parts}")

    verdict_index = None
    for i, part in enumerate(parts):
        token = part.lower()
        if token in PASS_TOKENS or token in FAIL_DESCRIPTIONS:
            verdict_index = i
            break

    if verdict_index is None:
        print(f"\n  ⚠ Nie znalazłem pola statusu w: {raw!r}")
        print("    Wklej ten output — dopiszemy status do tabeli.")
        return

    after = parts[verdict_index + 1:]

    if len(after) < 2:
        print(f"\n  ⚠ Rekord bez wartości pomiarowych: {raw!r}")
        return

    status = parts[verdict_index]
    current = after[0]          # meter 1 — POTWIERDZONE: prąd jest pierwszy
    resistance = after[1]       # meter 2
    duration = after[2] if len(after) > 2 else "—"

    print(f"\n  ┌─ WYNIK Ground Bond {'─' * 26}")
    print(f"  │  Status:      {status}")
    print(f"  │  Prąd:        {current} A       (meter 1)")
    print(f"  │  Rezystancja: {resistance} mΩ   (meter 2)")
    print(f"  │  Czas:        {duration} s")
    print(f"  └{'─' * 45}")

    # Kontrola zdrowego rozsądku: prąd powinien być bliski zadanemu.
    current_value = to_float(current)
    expected = profile["current"]

    if current_value is not None and expected:
        if abs(current_value - expected) > 0.35 * expected:
            print(f"\n  ⚠ Pierwsze pole ({current}) nie przypomina zadanego "
                  f"prądu {expected} A.")
            print("    Możliwe, że ten firmware ma odwrotną kolejność pól —")
            print("    ustaw hipot.gnd_field_order = \"resistance_first\".")

    if status.lower() in PASS_TOKENS:
        print("\n  ✔✔ PASS ✔✔")

        resistance_value = to_float(resistance)
        hi_limit = profile["hi_limit"]

        if resistance_value is not None and hi_limit:
            margin = 100.0 * (hi_limit - resistance_value) / hi_limit
            print(f"  Zapas do limitu: {margin:.0f} % "
                  f"({resistance_value} / {hi_limit} mΩ)")

            if margin <= 10:
                print("  ⚠ ZAPAS PONIŻEJ 10 % — sprawdź offset i kabel PE.")
                print("    Uruchom z --zero, żeby zmierzyć rezystancję toru.")
    else:
        reason = FAIL_DESCRIPTIONS.get(status.lower(), "przyczyna nieznana")
        print(f"\n  ✘✘ {status.upper()} ✘✘")
        print(f"  Znaczenie: {reason}")

        resistance_value = to_float(resistance)
        allowed = meter_range(profile["current"])

        if (resistance_value is not None and allowed
                and resistance_value >= allowed):
            print(f"  → Odczyt {resistance_value} mΩ = GÓRNA GRANICA ZAKRESU "
                  f"({allowed:.0f} mΩ przy {profile['current']} A).")
            print("    To znaczy 'poza zakresem', nie zmierzoną wartość.")
        elif resistance_value is not None:
            print(f"  → Rezystancja {resistance_value} mΩ vs HI limit "
                  f"{profile['hi_limit']} mΩ")

        print("  → Sprawdź kabel PE, styki przekaźnika i oprzyrządowanie")


def phase_zero(ser, profile):
    """Pomiar rezystancji własnej toru — kable zwarte, bez DUT."""
    divider("POMIAR TORU (bez DUT)")
    print("  Zewrzyj kable pomiarowe ze sobą (CURRENT OUTPUT ↔ RETURN),")
    print("  bez podłączonego wyrobu.\n")
    print("  Odczyt = rezystancja kabla + styków przekaźnika + oprzyrządowania.")
    print("  To jest wartość, która przy Offset = 0 wchodzi do KAŻDEGO wyniku.\n")

    input("  Naciśnij Enter, gdy kable są zwarte...")

    zero_profile = dict(profile)
    zero_profile["offset"] = 0

    if not phase_setup(ser, zero_profile, skip_sag=False):
        return
    if not phase_test(ser, zero_profile):
        return

    phase_result(ser, zero_profile)

    print("\n  Co z tym zrobić:")
    print("   • ta wartość to kandydat na nastawę Offset (zakres 0-100 mΩ),")
    print("   • ustawienie offsetu zmienia to, CO faktycznie mierzysz —")
    print("     potwierdź z właścicielem procesu / jakością przed zmianą,")
    print("   • zmiana profilu trafi do logs/config_audit.log.")


def cleanup(ser):
    divider("Cleanup")
    send(ser, "STOP", wait=0.3, label="STOP")
    send(ser, "SPR 0", wait=0.3, label="SPR 0 (Remote OFF)")
    send(ser, "RESET", wait=0.3, label="RESET")
    print("  Gotowe.")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Ground Bond — diagnostyka Slaughter 4320")
    parser.add_argument("--port", default=PORT, help=f"Port COM (domyślnie {PORT})")
    parser.add_argument("--skip-sag", action="store_true",
                        help="Pomiń SAG (jeśli krok 2 już jest typu GND)")
    parser.add_argument("--current", type=float, default=GND_PROFILE["current"],
                        help="Prąd GND w A (3.0 - 30.0)")
    parser.add_argument("--hi-limit", type=float, default=GND_PROFILE["hi_limit"],
                        help="HI limit w mΩ")
    parser.add_argument("--zero", action="store_true",
                        help="Pomiar rezystancji toru bez DUT (kable zwarte)")
    args = parser.parse_args()

    profile = dict(GND_PROFILE)
    profile["current"] = args.current
    profile["hi_limit"] = args.hi_limit

    print("\n" + "═" * 56)
    print("  GROUND BOND DIAGNOSTIC — Slaughter 4320")
    print(f"  Port: {args.port}  |  Baudrate: {BAUDRATE}")
    print(f"  Profil: {profile}")
    print(f"  Zakres limitu dla {profile['current']} A: "
          f"{meter_range(profile['current']):.0f} mΩ")
    print(f"  SAG: {'POMINIĘTY' if args.skip_sag else 'AKTYWNY'}")
    print(f"  Tryb: {'POMIAR TORU (bez DUT)' if args.zero else 'normalny'}")
    print("═" * 56)

    available = [p.device for p in serial.tools.list_ports.comports()]
    print(f"\nDostępne porty: {available}")

    if args.port not in available:
        print(f"\n✘ Port {args.port} nie jest dostępny!")
        print("  Użyj --port COMXX")
        return

    ser = None
    try:
        ser = serial.Serial(
            port=args.port, baudrate=BAUDRATE,
            bytesize=8, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT, rtscts=False, dsrdtr=False,
        )
        time.sleep(0.3)
        print(f"\n✔ Połączono z {args.port}")

        if not phase_connection(ser):
            return

        if args.zero:
            phase_zero(ser, profile)
            return

        if not phase_setup(ser, profile, skip_sag=args.skip_sag):
            cleanup(ser)
            return

        phase_verify_params(ser)

        print("\n  ⚠ UWAGA: przez tor popłynie "
              f"{profile['current']:.1f} A.")
        input("  Naciśnij Enter, aby uruchomić TEST...")

        if not phase_test(ser, profile):
            cleanup(ser)
            return

        phase_result(ser, profile)

    except serial.SerialException as e:
        print(f"\n✘ Błąd portu szeregowego: {e}")

    except KeyboardInterrupt:
        print("\n\n  Przerwano przez użytkownika.")

    finally:
        if ser and ser.is_open:
            try:
                cleanup(ser)
            except Exception:
                pass
            ser.close()
            print(f"  Port {args.port} zamknięty.")


if __name__ == "__main__":
    main()
