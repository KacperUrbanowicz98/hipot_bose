"""
sprawdz_projekt.py
------------------
Kontrola katalogu projektu PRZED buildem.

Odpowiada na trzy pytania:
  1. Czy są wszystkie pliki, których wymaga create_exe.py?
  2. Czy nie mam POMIESZANYCH WERSJI plików? (najgroźniejsze — część plików
     z nowej wersji, część ze starej, aplikacja startuje i cicho działa źle)
  3. Co w katalogu jest zbędne albo wrażliwe?

Uruchomienie:
    python sprawdz_projekt.py

Nic nie zmienia — tylko raportuje.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── Pliki wymagane przez aplikację (musi być zgodne z create_exe.PROJECT_FILES)
REQUIRED = [
    "main.py", "config.py", "verdict.py", "app_logging.py", "runtime_state.py",
    "login_screen.py", "main_screen.py", "hipot_controller.py",
    "relay_controller.py", "result_logger.py", "engineer_panel.py",
    "password_dialog.py", "ted_client.py",
]

BUILD_TOOLS = ["create_exe.py"]
OPTIONAL_USEFUL = ["config.json", "config.example.json", "set_engineer_password.py"]

# ── Znaczniki obecności poprawek 1.1.2+ ────────────────────────────────────
# Brak znacznika = plik jest ze starszej wersji.
MARKERS = {
    "verdict.py": ["ALL_VERDICT_TOKENS", "hi-limit", "ABORT_TOKENS"],
    "hipot_controller.py": ["GND_SLOTS", "V.ALL_VERDICT_TOKENS",
                            "describe_gnd_status", "HEARTBEAT_INTERVAL_S",
                            "_poll_for_result"],
    "main_screen.py": ["RELEASE_HINTS", "_set_hint", "expects_gnd"],
    "config.py": ["gnd_result_margin_s", "check_gnd_limit_vs_current",
                  "engineer_password_hash", "os.replace"],
    "result_logger.py": ["overall_verdict", "ResultLogError", "gnd_expected"],
    "ted_client.py": ["GND_MISSING", "flush_spool", "x-functions-key",
                      "expects_gnd"],
    "relay_controller.py": ["SWITCH_READ_TIMEOUT_S", "last_return_failed"],
    "app_logging.py": ["read_audit_tail", "_NullStream"],
    "runtime_state.py": ["test_in_progress", "guard_message"],
    "engineer_panel.py": ["check_gnd_limit_vs_current", "_blocked_by_test"],
    "password_dialog.py": ["verify_password", "_lock_remaining"],
    "login_screen.py": ["audit("],
    "main.py": ["setup_logging", "_open_engineer_panel"],
    "create_exe.py": ["allow_numbered", "runtime_state", "verdict"],
}

# ── Wzorce, które NIE POWINNY już występować ────────────────────────────────
FORBIDDEN = {
    "password_dialog.py": [
        (r'^\s*ENG_PASSWORD\s*=\s*["\']',
         "hasło inżynieryjne jawnie w kodzie (przypisanie, nie wzmianka)"),
    ],
    "hipot_controller.py": [
        (r"def _read_gnd_result", "stara metoda odczytu GND (bez pollingu)"),
        (r"min\(ramp \+ dwell", "min() skracający czekanie na wynik"),
    ],
}

FORBIDDEN_GLOBAL = [
    (r"\.configure\(\s*\{", "configure() ze słownikiem pozycyjnym — nie zadziała"),
    (r"self\.after\(\s*0\s*,\s*[\w\.]+configure\s*,\s*\{",
     "after(...configure, {...}) — słownik pozycyjny, nie zadziała"),
]

# ── Pliki zbędne / do archiwum ──────────────────────────────────────────────
JUNK = {
    "logger.py": "martwy kod zastąpiony przez result_logger.py — USUŃ",
    "niedzialajce3rzeczy.py": "nazwa mówi sama za siebie — do archiwum",
    "bezRTS.py": "eksperyment RS-232 — do archiwum, jeśli nieużywany",
    "hipot_test_connection.py": "zastąpiony zakładką Diagnostyka w panelu",
    "replacements.txt": "sprawdź, czy jeszcze potrzebny",
}

DIAGNOSTIC = ["ground_bond_test.py", "relay_test.py", "test_ted_send.py",
              "generate_doc_screenshots.py"]

GENERATED = ["dist", "build", "__pycache__", ".idea", ".venv", ".pytest_cache"]

SENSITIVE = [".env"]


class Report:
    def __init__(self):
        self.errors, self.warns, self.infos = [], [], []

    def err(self, m): self.errors.append(m)
    def warn(self, m): self.warns.append(m)
    def info(self, m): self.infos.append(m)


def header(t):
    print("\n" + "═" * 68)
    print(f"  {t}")
    print("═" * 68)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def check_required(r: Report):
    header("1. Pliki wymagane przez aplikację")
    missing = []

    for name in REQUIRED:
        if (ROOT / name).is_file():
            print(f"  [+] {name}")
        else:
            print(f"  [!] {name}  BRAK")
            missing.append(name)

    if missing:
        r.err(f"Brak plików aplikacji: {', '.join(missing)} — build się nie uda")
    else:
        print(f"\n  Komplet: {len(REQUIRED)}/{len(REQUIRED)} plików aplikacji.")

    for name in BUILD_TOOLS + OPTIONAL_USEFUL:
        if not (ROOT / name).is_file():
            r.warn(f"Brak {name}")


def check_duplicates(r: Report):
    header("2. Zdublowane / ponumerowane kopie plików")
    pattern = re.compile(r"^(.+)\((\d+)\)\.(py|json|txt)$", re.IGNORECASE)
    found = []

    for path in ROOT.iterdir():
        if path.is_file() and pattern.match(path.name):
            found.append(path.name)

    if found:
        print("  Znalezione:")
        for name in sorted(found):
            print(f"    [!] {name}")
        r.err(f"Ponumerowane kopie plików: {', '.join(sorted(found))}. "
              "Builder w trybie ścisłym je zignoruje, ale łatwo pomylić wersje "
              "— usuń albo przenieś do archiwum")
    else:
        print("  [+] Brak kopii typu 'main(2).py'.")


def check_versions(r: Report):
    header("3. SPÓJNOŚĆ WERSJI — czy pliki są z tej samej paczki")
    stale = []

    for name, markers in MARKERS.items():
        path = ROOT / name
        if not path.is_file():
            continue

        content = read(path)
        absent = [m for m in markers if m not in content]

        if absent:
            print(f"  [!] {name:<24} brak znaczników: {', '.join(absent)}")
            stale.append(name)
        else:
            print(f"  [+] {name:<24} aktualny")

    if stale:
        r.err("Pliki ze STARSZEJ wersji: " + ", ".join(stale) +
              ". Pomieszane wersje to najgroźniejszy przypadek — aplikacja "
              "wstanie i będzie cicho działać źle. Nadpisz je z najnowszej paczki")
    else:
        print("\n  [+] Wszystkie pliki mają znaczniki poprawek 1.1.2+.")


def check_forbidden(r: Report):
    header("4. Wzorce, które nie powinny już występować")
    hits = []

    for name, rules in FORBIDDEN.items():
        path = ROOT / name
        if not path.is_file():
            continue
        for num, line in enumerate(read(path).splitlines(), 1):
            for regex, desc in rules:
                if re.search(regex, line):
                    print(f"  [!] {name}:{num}: {desc}")
                    hits.append(f"{name}:{num}: {desc}")

    for path in sorted(ROOT.glob("*.py")):
        # Pomijamy sam skrypt kontrolny — zawiera te wzorce jako regexy.
        if path.name == Path(__file__).name:
            continue
        content = read(path)
        for regex, desc in FORBIDDEN_GLOBAL:
            for num, line in enumerate(content.splitlines(), 1):
                if re.search(regex, line):
                    print(f"  [!] {path.name}:{num}: {desc}")
                    hits.append(f"{path.name}:{num}: {desc}")

    if hits:
        r.err(f"Znaleziono {len(hits)} wzorców do usunięcia (szczegóły wyżej)")
    else:
        print("  [+] Czysto.")


def check_config(r: Report):
    header("5. config.json")
    path = ROOT / "config.json"

    if not path.is_file():
        print("  [~] Brak — aplikacja utworzy domyślny przy pierwszym starcie.")
        return

    try:
        cfg = json.loads(read(path))
    except json.JSONDecodeError as e:
        print(f"  [!] Niepoprawny JSON: {e}")
        r.err("config.json jest nieczytelny")
        return

    print("  [+] JSON poprawny.")

    for section in ("users", "profiles", "sn_prefix_map", "serial",
                    "integrations", "hipot", "security"):
        mark = "+" if section in cfg else "~"
        note = "" if section in cfg else "  (zostanie dopisana automatycznie)"
        print(f"  [{mark}] sekcja {section}{note}")

    users = cfg.get("users", {})
    profiles = cfg.get("profiles", {})
    sn_map = cfg.get("sn_prefix_map", {})

    print(f"\n  Użytkowników: {len(users)} | profili: {len(profiles)} | "
          f"prefiksów SN: {len(sn_map)}")

    if not users:
        r.warn("Brak użytkowników w config.json — nikt się nie zaloguje")

    # Prefiksy wskazujące na nieistniejący profil
    orphans = [p for p, k in sn_map.items() if k not in profiles]
    if orphans:
        print(f"  [!] Prefiksy bez profilu: {', '.join(orphans)}")
        r.warn(f"Prefiksy SN wskazujące na nieistniejący profil: "
               f"{', '.join(orphans)} — operator dostanie 'Nieznany SN'")

    # Liczby zapisane jako tekst w bloku ground_bond — realna przyczyna awarii
    for key, prof in profiles.items():
        gnd = prof.get("ground_bond")
        if not isinstance(gnd, dict):
            continue
        for field in ("current", "hi_limit", "lo_limit", "dwell", "offset"):
            value = gnd.get(field)
            if isinstance(value, str):
                print(f"  [!] profil {key}: ground_bond.{field} = {value!r} "
                      "(tekst zamiast liczby)")
                r.err(f"profil {key}: ground_bond.{field} jest tekstem — "
                      "w starej wersji wywracało to krok Ground Bond i sztuka "
                      "mogła pójść do TED jako PASS")
        for field in ("voltage", "hi_limit", "lo_limit", "ramp", "dwell"):
            if isinstance(prof.get(field), str):
                print(f"  [!] profil {key}: {field} jest tekstem")
                r.warn(f"profil {key}: {field} zapisane jako tekst — popraw na liczbę")

    relay_port = cfg.get("serial", {}).get("relay_port")
    needs_relay = [k for k, p in profiles.items() if p.get("ground_bond")]

    if needs_relay and not relay_port:
        print(f"  [!] Profile z Ground Bond ({', '.join(needs_relay)}), "
              "a relay_port nie jest ustawiony")
        r.err("Profile z Ground Bond wymagają serial.relay_port — bez niego "
              "test będzie zablokowany (celowo)")

    # ── Integracja TED ────────────────────────────────────────────────
    integrations = cfg.get("integrations", {})
    ted_enabled = integrations.get("ted_enabled")
    db_type = integrations.get("ted_db_type")
    env_exists = (ROOT / ".env").is_file()

    print(f"\n  TED: ted_enabled={ted_enabled} | ted_db_type={db_type!r} | "
          f".env={'jest' if env_exists else 'BRAK'}")

    if ted_enabled and not env_exists:
        print("  [!] TED włączony, a brak .env z TED_FUNCTION_KEY")
        r.err("TED jest włączony, ale nie ma .env z TED_FUNCTION_KEY — "
              "wyniki trafią do kolejki logs/ted_queue zamiast do TED. "
              "Builder też przerwie build")

    if ted_enabled and env_exists:
        content = read(ROOT / ".env")
        if "TED_FUNCTION_KEY" not in content:
            r.err(".env nie zawiera TED_FUNCTION_KEY — sprawdź nazwę zmiennej")

    if ted_enabled and db_type == "":
        r.info("TED zapisuje do TABEL PRODUKCYJNYCH (ted_db_type = \"\")")
    elif ted_enabled and db_type == "TEST":
        r.warn("TED zapisuje do tabel TESTOWYCH — jeśli to stanowisko "
               "produkcyjne, ustaw ted_db_type = \"\"")

    security = cfg.get("security", {})
    if security.get("must_change_password"):
        r.warn("Hasło inżynieryjne nie zostało jeszcze zmienione po migracji "
               "(security.must_change_password = true)")

    # Ground Bond z offsetem 0 — powód marginalnych wyników
    for key, prof in profiles.items():
        gnd = prof.get("ground_bond")
        if isinstance(gnd, dict) and not gnd.get("offset"):
            r.info(f"profil {key}: ground_bond.offset = 0 — rezystancja kabla "
                   "i oprzyrządowania wchodzi do pomiaru")


def check_junk(r: Report):
    header("6. Pliki zbędne, diagnostyczne i wrażliwe")

    print("  Zbędne / do archiwum:")
    any_junk = False
    for name, why in JUNK.items():
        if (ROOT / name).is_file():
            print(f"    [~] {name:<28} {why}")
            any_junk = True
            if name == "logger.py":
                r.err("logger.py nadal istnieje — usuń (martwy kod, buduje "
                      "nazwę pliku z niesanityzowanego SN)")
    if not any_junk:
        print("    [+] Brak.")

    print("\n  Diagnostyczne (NIE pakowane do EXE, zostają w repo):")
    for name in DIAGNOSTIC:
        if (ROOT / name).is_file():
            print(f"    [+] {name}")

    print("\n  Generowane (do .gitignore, można kasować):")
    for name in GENERATED:
        path = ROOT / name
        if path.exists():
            print(f"    [~] {name}")

    print("\n  Wrażliwe:")
    gitignore = read(ROOT / ".gitignore")
    for name in SENSITIVE:
        if (ROOT / name).is_file():
            ignored = ".env" in gitignore
            state = "jest w .gitignore" if ignored else "NIE MA GO w .gitignore"
            print(f"    [{'+' if ignored else '!'}] {name} — {state}")
            if not ignored:
                r.err(".env z kluczem TED nie jest w .gitignore — jeśli repo "
                      "było gdziekolwiek wypchnięte, klucz trzeba uznać za "
                      "ujawniony i poprosić IT o rotację")

    # Podfoldery, które wyglądają na stare kopie projektu
    print("\n  Podfoldery wyglądające na kopię projektu:")
    found_copy = False
    for path in ROOT.iterdir():
        if not path.is_dir() or path.name in GENERATED or path.name.startswith("."):
            continue
        if (path / "main.py").is_file() or (path / "hipot_controller.py").is_file():
            print(f"    [!] {path.name}/ zawiera kopię plików aplikacji")
            r.warn(f"Podfolder {path.name}/ zawiera kopię plików aplikacji — "
                   "łatwo pomylić, którą wersję edytujesz. Zarchiwizuj albo usuń")
            found_copy = True
    if not found_copy:
        print("    [+] Brak.")


def check_version(r: Report):
    header("7. Wersja buildu")
    content = read(ROOT / "create_exe.py")
    match = re.search(r'VERSION\s*=\s*"([\d.]+)"', content)

    if match:
        print(f"  create_exe.py -> VERSION = {match.group(1)}")
    else:
        r.warn("Nie znalazłem VERSION w create_exe.py")


def main() -> int:
    print("╔" + "═" * 66 + "╗")
    print("║  KONTROLA PROJEKTU HiPot Bose".ljust(67) + "║")
    print(f"║  {str(ROOT)[:62]:<64}║")
    print("╚" + "═" * 66 + "╝")

    r = Report()

    check_required(r)
    check_duplicates(r)
    check_versions(r)
    check_forbidden(r)
    check_config(r)
    check_junk(r)
    check_version(r)

    header("PODSUMOWANIE")

    for label, items, mark in (("BŁĘDY", r.errors, "!"),
                               ("OSTRZEŻENIA", r.warns, "~"),
                               ("INFORMACJE", r.infos, "i")):
        if items:
            print(f"\n{label} ({len(items)}):")
            for item in items:
                print(f"  [{mark}] {item}")

    if not (r.errors or r.warns):
        print("\n  [+] Katalog gotowy do buildu, nic nie wymaga uwagi.")
    elif not r.errors:
        print("\n  [+] Build się uda. Ostrzeżenia wyżej warto przejrzeć.")
    else:
        print("\n  [!] Napraw błędy przed buildem.")

    print()
    return 1 if r.errors else 0


if __name__ == "__main__":
    sys.exit(main())
