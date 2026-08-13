"""
config.py
---------
Konfiguracja aplikacji HiPot Bose.

Zmiany względem poprzedniej wersji:

  1. load_config() nie wywala aplikacji przy uszkodzonym config.json.
     Wcześniej JSONDecodeError leciał w górę — przy starcie aplikacja nie
     wstawała, a w _on_sn_change() wyjątek leciał przy KAŻDYM wciśnięciu
     klawisza w polu SN.

  2. save_config() zapisuje atomowo (tmp + os.replace) i trzyma kopię
     poprzedniej wersji. Wcześniej zapis szedł prosto do pliku docelowego —
     zanik zasilania w trakcie niszczył wszystkie profile i użytkowników.

  3. resolve_profile_for_sn() dopasowuje po NAJDŁUŻSZYM pasującym prefiksie.
     Wcześniej brał zawsze sn[:6], mimo że panel inżynieryjny pozwala dodać
     prefiks 4–8 znaków. Prefiksy o innej długości niż 6 nigdy nie działały,
     a inżynier dostawał zielone "✔ Dodano".

  4. Ścieżka config.json liczona względem katalogu aplikacji, nie CWD.

  5. Nowe sekcje: security (hash hasła inżynieryjnego), hipot (parametry
     czasowe i format odczytu wyniku GND).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

from app_logging import get_logger

log = get_logger(__name__)


# ── Paleta ─────────────────────────────────────────────────────────────────
COLORS = {
    "bg": "#1a1a1a",
    "surface": "#242424",
    "card": "#2d2d2d",
    "border": "#3a3a3a",
    "primary": "#0078d4",
    "fail": "#d13438",
    "warning": "#ca5010",
    "success": "#107c10",
    "text": "#ffffff",
    "muted": "#9d9d9d",
}


# ── Ścieżki ────────────────────────────────────────────────────────────────
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent


CONFIG_FILE = str(app_dir() / "config.json")


def config_path() -> Path:
    return Path(CONFIG_FILE)


# ── Domyślna konfiguracja ──────────────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    "users": {},
    "profiles": {},
    "sn_prefix_map": {},

    "serial": {
        "port": "COM11",
        "baudrate": 9600,
        "timeout": 3,
        "relay_port": None,
        "relay_baudrate": 115200,
    },

    "integrations": {
        "ted_enabled": False,
        "ted_db_type": "TEST",
        # Gdy True, brak potwierdzenia z TED blokuje zwolnienie sztuki.
        # Domyślnie False — decyzja procesowa, nie techniczna.
        "ted_required": False,
    },

    "hipot": {
        # Margines czasu DOPISYWANY do ramp+dwell. To NIE jest górny limit.
        # Wcześniej kod robił min(ramp+dwell+1.5, test_timeout), czyli przy
        # długim profilu SKRACAŁ czekanie i czytał wynik przy podanym napięciu.
        "result_margin_s": 30.0,

        # Co ile odpytywać RD n? o wynik po minimalnym czasie testu.
        "result_poll_interval_s": 0.5,

        # Odczekanie po potwierdzeniu wyniku, zanim ruszy przekaźnik.
        "relay_switch_delay_s": 1.0,

        # Profil wymaga Ground Bond, ale relay_port nie jest ustawiony:
        #   True  -> test zostaje przerwany (zalecane)
        #   False -> stare zachowanie, GND leci bez przełączenia na PE
        "require_relay_for_gnd": True,

        # Kolejność pól rezystancja/prąd w odpowiedzi RD n? dla Ground Bond.
        #   "auto"             -> rozpoznanie po zaprogramowanym prądzie GND
        #   "current_first"    -> ...,<verdict>,<current>,<resistance>,...
        #   "resistance_first" -> ...,<verdict>,<resistance>,<current>,...
        # Dokumentacja i kod w repo opisywały to sprzecznie, dlatego "auto".
        "gnd_field_order": "auto",

        # Tolerancja rozpoznania prądu GND przy gnd_field_order="auto".
        "gnd_current_tolerance": 0.35,

        # ── Odpytywanie statusu testera (SA?) ──────────────────────────────
        # DO UZUPEŁNIENIA NA STANOWISKU. Dopóki listy są puste, kod opiera się
        # wyłącznie na pollingu RD n? — działa, ale bez dodatkowego
        # potwierdzenia z rejestru statusu.
        # Podejrzyj realne odpowiedzi: Panel Inżynieryjny -> Diagnostyka -> SA?
        "status_query": "SA?",
        "status_busy_tokens": [],   # np. ["TESTING", "RUN", "RAMP", "DWELL"]
        "status_idle_tokens": [],   # np. ["READY", "STOP", "IDLE"]
    },

    "security": {
        # Hasło inżynieryjne. Hash PBKDF2-HMAC-SHA256, nie plaintext.
        # Bootstrap odpowiada dotychczasowemu hasłu, żeby wdrożenie nie
        # odcięło dostępu — must_change wymusza zmianę przy pierwszym wejściu.
        "engineer_password_hash": "",
        "engineer_password_salt": "",
        "engineer_password_iterations": 240000,
        "must_change_password": True,
        "max_password_attempts": 5,
        "lockout_seconds": 60,
    },
}


# ── Cache + synchronizacja ─────────────────────────────────────────────────
_lock = threading.RLock()
_cache: dict | None = None
_cache_mtime: float | None = None


def _deep_merge_defaults(config: dict, defaults: dict) -> dict:
    """Uzupełnia brakujące klucze wartościami domyślnymi, nie nadpisując istniejących."""
    for key, value in defaults.items():
        if key not in config:
            config[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(config.get(key), dict):
            _deep_merge_defaults(config[key], value)
    return config


def _quarantine_broken_file(path: Path, reason: str) -> None:
    """Odsuwa uszkodzony config.json na bok, zamiast go nadpisać."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = path.with_name(f"{path.stem}.broken_{stamp}{path.suffix}")

    try:
        shutil.copy2(path, target)
        log.error("Uszkodzony config.json (%s) — kopia: %s", reason, target)
    except OSError as e:
        log.error("Uszkodzony config.json (%s), nie udało się zrobić kopii: %s",
                  reason, e)


def _try_restore_backup(path: Path) -> dict | None:
    """Próbuje wczytać ostatnią dobrą kopię config.json.bak."""
    backup = path.with_suffix(path.suffix + ".bak")

    if not backup.exists():
        return None

    try:
        with backup.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            log.warning("Przywrócono konfigurację z kopii: %s", backup)
            return data

    except (OSError, json.JSONDecodeError) as e:
        log.error("Kopia %s też jest nieczytelna: %s", backup, e)

    return None


def load_config(force_reload: bool = False) -> dict:
    """
    Wczytuje konfigurację.

    Zawsze zwraca poprawny słownik — nigdy nie rzuca wyjątku do UI.
    Zwracany obiekt jest KOPIĄ, więc modyfikacja u wywołującego nie psuje cache.
    """
    global _cache, _cache_mtime

    path = config_path()

    with _lock:
        if not path.exists():
            log.warning("Brak %s — tworzę domyślną konfigurację.", path)
            default = copy.deepcopy(DEFAULT_CONFIG)
            _bootstrap_security(default)
            save_config(default)
            return copy.deepcopy(default)

        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None

        if (not force_reload and _cache is not None
                and mtime is not None and mtime == _cache_mtime):
            return copy.deepcopy(_cache)

        data: dict | None = None

        try:
            with path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)

            if isinstance(loaded, dict):
                data = loaded
            else:
                _quarantine_broken_file(path, "plik nie zawiera obiektu JSON")

        except json.JSONDecodeError as e:
            _quarantine_broken_file(path, f"niepoprawny JSON: {e}")

        except OSError as e:
            log.error("Nie można odczytać %s: %s", path, e)

        if data is None:
            data = _try_restore_backup(path)

        if data is None:
            log.error("Startuję na konfiguracji domyślnej — "
                      "profile i użytkownicy są NIEDOSTĘPNE.")
            data = copy.deepcopy(DEFAULT_CONFIG)

        _deep_merge_defaults(data, DEFAULT_CONFIG)
        _bootstrap_security(data)

        _cache = copy.deepcopy(data)
        _cache_mtime = mtime

        return copy.deepcopy(data)


def save_config(config: dict) -> bool:
    """
    Zapisuje konfigurację atomowo.

    Kolejność:
        1. zapis do config.json.tmp + flush + fsync
        2. kopia obecnego config.json -> config.json.bak
        3. os.replace(tmp, config.json)   <- operacja atomowa

    Dzięki temu przerwanie w dowolnym momencie zostawia albo starą, albo nową
    wersję pliku — nigdy uciętej.

    Zwraca True przy powodzeniu.
    """
    global _cache, _cache_mtime

    path = config_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    backup_path = path.with_suffix(path.suffix + ".bak")

    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            if path.exists():
                try:
                    shutil.copy2(path, backup_path)
                except OSError as e:
                    log.warning("Nie udało się zrobić kopii %s: %s", backup_path, e)

            os.replace(tmp_path, path)

            _cache = copy.deepcopy(config)
            try:
                _cache_mtime = path.stat().st_mtime
            except OSError:
                _cache_mtime = None

            log.info("Konfiguracja zapisana: %s", path)
            return True

        except OSError as e:
            log.error("BŁĄD zapisu konfiguracji %s: %s", path, e)

            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

            return False


def get_section(name: str) -> dict:
    """Skrót do sekcji konfiguracji z gwarancją słownika."""
    section = load_config().get(name, {})
    return section if isinstance(section, dict) else {}


# ── Bezpieczeństwo: hasło inżynieryjne ─────────────────────────────────────
#: Dotychczasowe hasło zaszyte w password_dialog.py. Używane WYŁĄCZNIE raz,
#: przy migracji, żeby wdrożenie nowej wersji nie odcięło dostępu do panelu.
#: Po pierwszym uruchomieniu w config.json jest już tylko hash, a flaga
#: must_change_password wymusza zmianę.
_LEGACY_BOOTSTRAP_PASSWORD = "bose2024"


def hash_password(password: str, salt_hex: str | None = None,
                  iterations: int = 240000) -> tuple[str, str, int]:
    """
    Zwraca (hash_hex, salt_hex, iterations).

    PBKDF2-HMAC-SHA256 ze standardowej biblioteki — bez dodatkowych zależności
    w buildzie.
    """
    import hashlib
    import secrets

    if salt_hex:
        salt = bytes.fromhex(salt_hex)
    else:
        salt = secrets.token_bytes(16)
        salt_hex = salt.hex()

    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )

    return digest.hex(), salt_hex, iterations


def verify_password(password: str, config: dict | None = None) -> bool:
    """Stałoczasowe porównanie hasła z hashem z konfiguracji."""
    import hmac

    security = (config or load_config()).get("security", {})

    stored = security.get("engineer_password_hash", "")
    salt = security.get("engineer_password_salt", "")
    iterations = int(security.get("engineer_password_iterations", 240000))

    if not stored or not salt:
        return False

    candidate, _, _ = hash_password(password, salt, iterations)

    return hmac.compare_digest(candidate, stored)


def set_password(password: str, config: dict | None = None) -> bool:
    """Ustawia nowe hasło inżynieryjne i zdejmuje flagę must_change."""
    cfg = config if config is not None else load_config()

    digest, salt, iterations = hash_password(password)

    cfg.setdefault("security", {}).update({
        "engineer_password_hash": digest,
        "engineer_password_salt": salt,
        "engineer_password_iterations": iterations,
        "must_change_password": False,
    })

    return save_config(cfg)


def _bootstrap_security(config: dict) -> None:
    """
    Jednorazowa migracja: jeżeli w config.json nie ma jeszcze hasha,
    wpisujemy hash dotychczasowego hasła i podnosimy must_change_password.

    Dzięki temu aktualizacja aplikacji nie odcina nikogo od panelu, a hasło
    przestaje być czytelnym stringiem w EXE.
    """
    security = config.setdefault("security", {})

    if security.get("engineer_password_hash"):
        return

    digest, salt, iterations = hash_password(_LEGACY_BOOTSTRAP_PASSWORD)

    security["engineer_password_hash"] = digest
    security["engineer_password_salt"] = salt
    security["engineer_password_iterations"] = iterations
    security["must_change_password"] = True

    log.warning(
        "Zmigrowano hasło inżynieryjne do postaci hash. "
        "Ustaw nowe hasło w panelu — zakładka Bezpieczeństwo."
    )


def must_change_password() -> bool:
    return bool(get_section("security").get("must_change_password", False))


# ── Dopasowanie profilu do SN ──────────────────────────────────────────────
def resolve_profile_for_sn(sn: str) -> tuple:
    """
    Zwraca (klucz_profilu, profil) albo (None, None).

    Dopasowanie po NAJDŁUŻSZYM pasującym prefiksie. Panel inżynieryjny
    dopuszcza prefiksy 4–8 znaków; poprzednia wersja porównywała wyłącznie
    sn[:6], więc prefiksy innej długości nigdy nie zadziałały.

    Najdłuższy prefiks wygrywa, żeby wyjątek typu "F100019" miał pierwszeństwo
    przed ogólnym "F1000".
    """
    if not sn:
        return None, None

    sn = sn.strip()

    config = load_config()
    sn_map = config.get("sn_prefix_map", {})
    profiles = config.get("profiles", {})

    if not isinstance(sn_map, dict) or not isinstance(profiles, dict):
        log.error("Uszkodzona struktura sn_prefix_map/profiles w config.json")
        return None, None

    for prefix in sorted(sn_map.keys(), key=len, reverse=True):
        if not prefix:
            continue

        if sn.upper().startswith(str(prefix).upper()):
            profile_key = sn_map.get(prefix)
            profile = profiles.get(profile_key)

            if profile:
                return profile_key, profile

            log.warning(
                "Prefiks %r wskazuje na nieistniejący profil %r — "
                "sprawdź SN Prefix Map.", prefix, profile_key
            )

    return None, None


# ── Walidacja profilu ──────────────────────────────────────────────────────
#: Granice akceptowane przez aplikację. Nie zastępują ograniczeń testera —
#: mają wyłapać oczywiste pomyłki wpisania (przecinek, zera, znak minus).
LIMITS = {
    "voltage":      (0.1, 5.0),      # kV
    "hi_limit":     (0.01, 100.0),   # mA
    "lo_limit":     (0.0, 100.0),    # mA
    "ramp":         (0.1, 999.0),    # s
    "dwell":        (0.2, 999.0),    # s
    "gnd_current":  (1.0, 40.0),     # A
    "gnd_hi_limit": (1.0, 1000.0),   # mΩ
    "gnd_lo_limit": (0.0, 1000.0),   # mΩ
    "gnd_dwell":    (0.2, 999.0),    # s
    "gnd_offset":   (0.0, 1000.0),   # mΩ
}


def check_range(field: str, value: float) -> str | None:
    """Zwraca komunikat błędu albo None, jeśli wartość mieści się w zakresie."""
    if field not in LIMITS:
        return None

    low, high = LIMITS[field]

    if value < low or value > high:
        return f"{field}: wartość {value} poza zakresem {low}–{high}"

    return None
