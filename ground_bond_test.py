"""
ground_bond_test.py
-------------------
Izolowany skrypt testowy dla Ground Bond na Slaughter 4320.
Uruchom PRZED wprowadzeniem zmian w hipot_controller.py.

Testuje sekwencję:
  RESET → SPR 1 → FL 1 → SS 2 → SAG → EC → EH → EL → EDW → EO → EF → TEST → RD 2?

Użycie:
  python ground_bond_test.py
  python ground_bond_test.py --port COM12
  python ground_bond_test.py --port COM12 --skip-sag
"""

import serial
import serial.tools.list_ports
import time
import argparse

# ── Parametry testu ────────────────────────────────────────────────────────
PORT     = "COM11"   # ← zmień na swój port
BAUDRATE = 9600
TIMEOUT  = 3

GND_PROFILE = {
    "current":   10.0,   # A
    "hi_limit":  100,    # mΩ
    "lo_limit":  0,      # mΩ
    "dwell":     1.0,    # s
    "offset":    0,      # mΩ
    "frequency": 1,      # 1=60Hz, 0=50Hz
}

STEP = 2   # krok Ground Bond w Memory 1


# ── Helpers ────────────────────────────────────────────────────────────────
def send(ser, cmd, wait=0.6, label=None):
    """Wysyła komendę, czeka, zwraca surowe bajty."""
    ser.reset_input_buffer()
    ser.write((cmd.strip() + "\r\n").encode("ascii"))
    time.sleep(wait)
    resp = ser.read_all()
    ack  = "✔ ACK" if b'\x06' in resp else ("✘ NAK" if b'\x15' in resp else "— brak odpowiedzi")
    tag  = label or cmd
    print(f"  {tag:<22} → {ack}   raw={resp!r}")
    return resp


def query(ser, cmd, wait=0.6):
    """Wysyła zapytanie, zwraca string."""
    ser.reset_input_buffer()
    ser.write((cmd.strip() + "\r\n").encode("ascii"))
    time.sleep(wait)
    raw    = ser.read_until(b'\n', size=512)
    result = raw.decode("ascii", errors="replace").strip()
    print(f"  {cmd:<22} → {result!r}")
    return result


# ── NOWE: surowy odczyt bajty (dla RD N? który może zwrócić NAK bez \n) ───
def query_raw(ser, cmd, wait=1.0):
    """Wysyła zapytanie, czeka wait sekund, zwraca surowe bajty i string."""
    ser.reset_input_buffer()
    ser.write((cmd.strip() + "\r\n").encode("ascii"))
    time.sleep(wait)
    raw_bytes = ser.read_all()
    raw_str   = raw_bytes.decode("ascii", errors="replace").strip()
    print(f"  {cmd:<22} → raw_bytes={raw_bytes!r}  decoded={raw_str!r}")
    return raw_bytes, raw_str


def cmd_ok(ser, command, wait=0.6, label=None):
    """Zwraca True jeśli dostał ACK (0x06)."""
    resp = send(ser, command, wait, label)
    return b'\x06' in resp


def divider(title=""):
    print(f"\n{'─'*52}")
    if title:
        print(f"  {title}")
    print(f"{'─'*52}")


# ── Fazy testu ─────────────────────────────────────────────────────────────
def phase_connection(ser):
    divider("FAZA 1: Sprawdzenie połączenia")
    resp = send(ser, "SA?", wait=0.6, label="SA? (status)")
    if len(resp) == 0:
        print("  ⚠ Brak odpowiedzi na SA? — sprawdź port i kabel RS-232")
        return False
    print(f"  ✔ Tester odpowiada: {resp!r}")
    return True


def phase_setup(ser, skip_sag=False):
    divider(f"FAZA 2: Konfiguracja Ground Bond (krok {STEP})")
    p = GND_PROFILE

    steps = [
        ("RESET",                  0.4),
        ("SPR 1",                  0.6),
        ("FL 1",                   0.3),
        (f"SS {STEP}",             0.3),
    ]
    if not skip_sag:
        steps.append(("SAG",       0.3))

    steps += [
        (f"EC {p['current']:.2f}", 0.3),
        (f"EH {p['hi_limit']}",    0.3),
        (f"EL {p['lo_limit']}",    0.3),
        (f"EDW {p['dwell']:.1f}",  0.3),
        (f"EO {p['offset']}",      0.3),
        (f"EF {p['frequency']}",   0.3),
    ]

    for cmd, wait in steps:
        if cmd == "RESET":
            send(ser, cmd, wait)   # RESET nie zwraca ACK
            continue
        if not cmd_ok(ser, cmd, wait):
            print(f"\n  ✘ PROBLEM: '{cmd}' nie dostał ACK — zatrzymuję")
            return False

    print("\n  ✔ Wszystkie parametry zaakceptowane")
    return True


def phase_verify_params(ser):
    """Odczyt wgranych parametrów przez LS (Load Step query)."""
    divider("FAZA 3: Weryfikacja wgranych parametrów (LS 2?)")
    raw = query(ser, f"LS {STEP}?", wait=1.0)
    if raw:
        print(f"  Parametry kroku {STEP}: {raw}")
    else:
        print("  ⚠ LS? nie odpowiedział — komenda może nie być obsługiwana")


def phase_test(ser):
    divider("FAZA 4: Uruchomienie TEST")
    if not cmd_ok(ser, "TEST", wait=0.5):
        print("  ✘ TEST — brak ACK")
        print("  Możliwe przyczyny:")
        print("    • Interlock aktywny (sprawdź wtyczkę interlockową z tyłu testera)")
        print("    • Tester nie jest w trybie Remote (SPR 1 nie zadziałał)")
        print("    • Brak DUT na CURRENT OUTPUT (może blokować start)")
        return False

    wait_time = GND_PROFILE["dwell"] + 1.5
    print(f"  Test w toku... czekam {wait_time:.1f}s")
    steps = 20
    for i in range(steps + 1):
        bar = "█" * i + "░" * (steps - i)
        print(f"  [{bar}] {i * 5}%", end="\r")
        time.sleep(wait_time / steps)
    print(f"  [{'█' * steps}] 100%")
    return True


def phase_result(ser):
    divider(f"FAZA 5: Odczyt wyniku RD {STEP}?")

    # ── ZMIANA 1: sprawdź status SA? przed odczytem wyniku ────────────────
    print("  [pre-check] SA? po teście:")
    send(ser, "SA?", wait=0.6, label="SA? (post-test)")

    # ── ZMIANA 2: query_raw z wait=1.0 (zamiast query z wait=0.5) ─────────
    raw_bytes, raw = query_raw(ser, f"RD {STEP}?", wait=1.0)

    # ── ZMIANA 3: obsługa NAK jako osobny przypadek ────────────────────────
    if b'\x15' in raw_bytes and len(raw_bytes) <= 3:
        print(f"\n  ⚠ NAK na RD {STEP}? — tester nie ma wyniku dla kroku {STEP}")
        print("    Możliwe przyczyny:")
        print("    • Test ukończony bez DUT — brak rzeczywistego pomiaru")
        print("    • Tester zresetował wynik przed odczytem")
        print("    • Spróbuj uruchomić z podłączonym DUT (kabel CURRENT OUTPUT↔RETURN)")
        print()

        # ── ZMIANA 4: fallback — spróbuj RD? (globalny, ostatni wynik) ───
        print("  [fallback] Próbuję RD? (globalny ostatni wynik)...")
        fb_bytes, fb_str = query_raw(ser, "RD?", wait=0.8)
        if b'\x15' not in fb_bytes and fb_str:
            print(f"  RD? zwrócił: {fb_str!r}")
            _parse_gnd_result(fb_str)
        else:
            print("  RD? również NAK — brak zapisanego wyniku w testerze")

        # ── ZMIANA 5: spróbuj też RD 1? (krok 1, gdyby step był inny) ────
        print()
        print("  [fallback] Próbuję RD 1? (krok 1)...")
        fb1_bytes, fb1_str = query_raw(ser, "RD 1?", wait=0.8)
        if b'\x15' not in fb1_bytes and fb1_str:
            print(f"  RD 1? zwrócił: {fb1_str!r}")
        else:
            print("  RD 1? również NAK")
        return

    # ── Normalny parsing ───────────────────────────────────────────────────
    _parse_gnd_result(raw)


def _parse_gnd_result(raw):
    """Parser wyniku Ground Bond. Obsługuje formaty z 6+ polami."""
    parts = [p.strip() for p in raw.split(",")]
    print(f"  Pola ({len(parts)}): {parts}")

    # Format spodziewany: GND,<step>,<Pass|Fail>,<resistance_mΩ>,<current_A>,<time_s>
    if len(parts) >= 6:
        verdict    = parts[2]
        resistance = parts[3]
        current    = parts[4]
        t          = parts[5]

        print(f"\n  ┌─ WYNIK Ground Bond {'─'*22}")
        print(f"  │  Verdict:     {verdict}")
        print(f"  │  Rezystancja: {resistance} mΩ")
        print(f"  │  Prąd:        {current} A")
        print(f"  │  Czas:        {t} s")
        print(f"  └{'─'*40}")

        if verdict == "Pass":
            print("\n  ✔✔ PASS ✔✔")
        else:
            print("\n  ✘✘ FAIL ✘✘")
            try:
                res_val = float(resistance)
                if res_val > GND_PROFILE["hi_limit"]:
                    print(f"  → Rezystancja {res_val} mΩ > HI limit {GND_PROFILE['hi_limit']} mΩ")
            except ValueError:
                pass
            print("  → Sprawdź podłączenie DUT i kabel prądowy (CURRENT OUTPUT)")

    # ── ZMIANA 6: fallback dla formatu bez pola typu (np. "2,Pass,15.3,...") ──
    elif len(parts) >= 5:
        print("  [uwaga] Format bez prefiksu GND — próbuję offset o 1 pole wcześniej")
        verdict    = parts[1]
        resistance = parts[2]
        current    = parts[3]
        t          = parts[4]

        print(f"\n  ┌─ WYNIK Ground Bond (alt. format) {'─'*16}")
        print(f"  │  Verdict:     {verdict}")
        print(f"  │  Rezystancja: {resistance} mΩ")
        print(f"  │  Prąd:        {current} A")
        print(f"  │  Czas:        {t} s")
        print(f"  └{'─'*40}")

        if verdict == "Pass":
            print("\n  ✔✔ PASS ✔✔")
        else:
            print("\n  ✘✘ FAIL ✘✘")

    else:
        print(f"\n  ⚠ Nieoczekiwany format wyniku: {raw!r}")
        print("  → Wklej ten output — poprawimy parser pod konkretny format testera")


def cleanup(ser):
    divider("Cleanup")
    send(ser, "SPR 0", wait=0.3, label="SPR 0 (Remote OFF)")
    send(ser, "RESET",  wait=0.3, label="RESET")
    print("  Gotowe.")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Ground Bond test diagnostyczny — Slaughter 4320")
    parser.add_argument("--port",     default=PORT,
                        help=f"Port COM (domyślnie {PORT})")
    parser.add_argument("--skip-sag", action="store_true",
                        help="Pomiń komendę SAG (jeśli krok 2 już ustawiony jako GND)")
    args = parser.parse_args()

    print("\n" + "═"*52)
    print("  GROUND BOND DIAGNOSTIC — Slaughter 4320")
    print(f"  Port: {args.port}  |  Baudrate: {BAUDRATE}")
    print(f"  Profil: {GND_PROFILE}")
    print(f"  SAG: {'POMINIĘTY (--skip-sag)' if args.skip_sag else 'AKTYWNY'}")
    print("═"*52)

    available = [p.device for p in serial.tools.list_ports.comports()]
    print(f"\nDostępne porty: {available}")
    if args.port not in available:
        print(f"\n✘ Port {args.port} nie jest dostępny!")
        print(f"  Zmień PORT w skrypcie lub użyj --port COMXX")
        return

    ser = None
    try:
        ser = serial.Serial(
            port=args.port, baudrate=BAUDRATE,
            bytesize=8, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT, rtscts=False, dsrdtr=False
        )
        time.sleep(0.3)
        print(f"\n✔ Połączono z {args.port}")

        if not phase_connection(ser):
            return

        if not phase_setup(ser, skip_sag=args.skip_sag):
            cleanup(ser)
            return

        phase_verify_params(ser)

        input("\n  ⚠ Naciśnij Enter aby uruchomić TEST (DUT nie jest wymagany do tego testu)...")

        if not phase_test(ser):
            cleanup(ser)
            return

        phase_result(ser)

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