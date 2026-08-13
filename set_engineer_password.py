"""
set_engineer_password.py
------------------------
Narzędzie serwisowe: ustawia hasło inżynieryjne w config.json.

Nie jest pakowane do EXE. Uruchamiaj obok config.json na stanowisku
albo na maszynie przygotowującej konfigurację.

Użycie:
    python set_engineer_password.py

Hasło jest wczytywane bez echa (getpass) i zapisywane wyłącznie jako
hash PBKDF2-HMAC-SHA256. W config.json nie ma nigdzie jawnego hasła.

To samo da się zrobić z poziomu aplikacji:
    Panel Inżynieryjny → Bezpieczeństwo → Zmień hasło
"""

from __future__ import annotations

import getpass
import sys

from config import config_path, load_config, set_password, verify_password

MIN_LENGTH = 10


def main() -> int:
    print("=" * 60)
    print("  Ustawienie hasła inżynieryjnego — HiPot Bose")
    print(f"  Plik konfiguracji: {config_path()}")
    print("=" * 60)

    config = load_config()

    security = config.get("security", {})

    if security.get("engineer_password_hash"):
        current = getpass.getpass("Obecne hasło: ")

        if not verify_password(current, config):
            print("\n✘ Obecne hasło jest nieprawidłowe. Przerywam.")
            return 1

    new = getpass.getpass(f"Nowe hasło (min. {MIN_LENGTH} znaków): ")

    if len(new) < MIN_LENGTH:
        print(f"\n✘ Hasło musi mieć co najmniej {MIN_LENGTH} znaków.")
        return 1

    repeat = getpass.getpass("Powtórz nowe hasło: ")

    if new != repeat:
        print("\n✘ Hasła nie są identyczne.")
        return 1

    if not set_password(new, config):
        print("\n✘ Nie udało się zapisać config.json — sprawdź uprawnienia.")
        return 1

    print("\n✔ Hasło zapisane jako hash w config.json.")
    print("  Flaga must_change_password została zdjęta.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nPrzerwano.")
        sys.exit(130)
