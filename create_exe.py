"""
create_exe.py
-------------
Builder EXE dla aplikacji HiPot Bose.

Buduje aplikację PyInstallerem w trybie ONEDIR.

Zmiany:
  - nowe moduły w PROJECT_FILES i HIDDEN_IMPORTS
    (verdict, app_logging, runtime_state),
  - domyślnie TRYB ŚCISŁY: builder nie akceptuje plików typu main(3).py.
    Poprzednia wersja wybierała "najnowszy" wariant, co przy plikach
    pobranych z przeglądarki groziło zbudowaniem EXE z przypadkowej wersji.
    Stare zachowanie: python create_exe.py --allow-numbered
  - .env kopiowany do release (TED aktywny), z kontrolą spójności
    względem integrations.ted_enabled w config.json,
  - build_manifest.txt zawiera listę zbudowanych plików źródłowych,
  - --console do buildu diagnostycznego (widoczna konsola).

Uruchomienie:
    python create_exe.py
    python create_exe.py --allow-numbered
    python create_exe.py --console

Wynik:
    dist/HiPot Bose/HiPot Bose.exe
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


# ============================================================
# DANE APLIKACJI
# ============================================================

APP_NAME = "HiPot Bose"
APP_DESCRIPTION = "HiPot Bose Production Tester"
COMPANY_NAME = "Reconext"
VERSION = "1.1.7.0"
COPYRIGHT = "Reconext 2026"

ROOT_DIR = Path(__file__).resolve().parent
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
STAGING_DIR = BUILD_DIR / "_hipot_bose_staging"
OUTPUT_DIR = DIST_DIR / APP_NAME
EXE_PATH = OUTPUT_DIR / f"{APP_NAME}.exe"


# ============================================================
# PLIKI PROJEKTU
# ============================================================
# Tylko pliki wymagane przez właściwą aplikację.
# Nie pakujemy plików diagnostycznych:
#   ground_bond_test.py, relay_test.py, hipot_test_connection.py,
#   test_ted_send.py, set_engineer_password.py
#
# logger.py został USUNIĘTY z projektu — był martwym kodem zastąpionym
# przez result_logger.py i budował nazwę pliku z niesanityzowanego SN.

PROJECT_FILES = [
    "main.py",
    "config.py",
    "verdict.py",
    "app_logging.py",
    "runtime_state.py",
    "login_screen.py",
    "main_screen.py",
    "hipot_controller.py",
    "relay_controller.py",
    "result_logger.py",
    "engineer_panel.py",
    "password_dialog.py",
    "ted_client.py",
]

EDITABLE_DATA_FILES = [
    "config.json",
]

# .env z TED_FUNCTION_KEY jest kopiowany obok EXE, bo TED jest AKTYWNY.
# Bez tego pliku aplikacja nie ma klucza funkcji i każdy wynik ląduje
# w kolejce logs/ted_queue zamiast trafić do TED.
OPTIONAL_DATA_FILES = [
    ".env",
]

OPTIONAL_ICONS = [
    "hipot_bose.ico",
    "app.ico",
    "icon.ico",
]


# ============================================================
# IMPORTY DLA PYINSTALLERA
# ============================================================

HIDDEN_IMPORTS = [
    # GUI / Tk
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "tkinter.filedialog",
    "customtkinter",
    "darkdetect",
    "packaging",
    "packaging.version",

    # Serial / RS-232
    "serial",
    "serial.tools.list_ports",
    "serial.tools.list_ports_windows",

    # Logowanie
    "logging.handlers",

    # Opcjonalne .env dla TED
    "dotenv",

    # Moduły aplikacji
    "config",
    "verdict",
    "app_logging",
    "runtime_state",
    "login_screen",
    "main_screen",
    "hipot_controller",
    "relay_controller",
    "result_logger",
    "engineer_panel",
    "password_dialog",
    "ted_client",
]


class BuildError(RuntimeError):
    pass


# ============================================================
# POMOCNICZE
# ============================================================

def print_header(title: str) -> None:
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)


def run_command(command: list[str], cwd: Optional[Path] = None) -> None:
    print("[*] " + subprocess.list2cmdline(command))

    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise BuildError(
            f"Polecenie zakończyło się błędem {result.returncode}: {command[0]}"
        )


def ensure_package(import_name: str, pip_name: str) -> None:
    if importlib.util.find_spec(import_name) is not None:
        return

    print(f"[~] Brak pakietu {pip_name}. Instaluję...")
    run_command([sys.executable, "-m", "pip", "install", pip_name])


def numeric_suffix(path: Path, canonical_name: str) -> int:
    if path.name.lower() == canonical_name.lower():
        return 10 ** 9

    canonical = Path(canonical_name)
    pattern = re.compile(
        rf"^{re.escape(canonical.stem)}\((\d+)\){re.escape(canonical.suffix)}$",
        re.IGNORECASE,
    )

    match = pattern.match(path.name)
    return int(match.group(1)) if match else -1


def resolve_project_file(
    canonical_name: str,
    required: bool = True,
    allow_numbered: bool = False,
) -> Optional[Path]:
    """
    Szuka pliku projektu.

    W trybie ścisłym (domyślnym) akceptuje wyłącznie dokładną nazwę.
    --allow-numbered przywraca stare zachowanie z main(1).py, main(5).py,
    ale to jest wygoda developerska, nie tryb produkcyjny.
    """
    exact = ROOT_DIR / canonical_name

    if exact.is_file():
        return exact

    if not allow_numbered:
        if required:
            raise BuildError(
                f"Brak wymaganego pliku: {canonical_name}. "
                "Jeśli masz wariant typu 'main(2).py', zmień mu nazwę albo "
                "uruchom builder z --allow-numbered."
            )
        return None

    canonical = Path(canonical_name)
    pattern = re.compile(
        rf"^{re.escape(canonical.stem)}(?:\((\d+)\))?{re.escape(canonical.suffix)}$",
        re.IGNORECASE,
    )

    candidates = [
        path for path in ROOT_DIR.iterdir()
        if path.is_file() and pattern.match(path.name)
    ]

    if candidates:
        selected = max(
            candidates,
            key=lambda p: (numeric_suffix(p, canonical_name), p.stat().st_mtime),
        )
        print(f"[!] {canonical_name}: używam {selected.name} "
              f"(tryb --allow-numbered)")
        return selected

    if required:
        raise BuildError(f"Brak wymaganego pliku: {canonical_name}")

    return None


def resolve_icon(allow_numbered: bool) -> Optional[Path]:
    for icon_name in OPTIONAL_ICONS:
        icon = resolve_project_file(icon_name, required=False,
                                    allow_numbered=allow_numbered)
        if icon:
            return icon
    return None


# ============================================================
# VERSION INFO
# ============================================================

def create_version_file(path: Path) -> None:
    version_tuple = tuple(int(part) for part in VERSION.split("."))

    if len(version_tuple) != 4:
        raise BuildError("VERSION musi mieć format np. 1.0.0.0")

    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{COMPANY_NAME}'),
        StringStruct('FileDescription', '{APP_DESCRIPTION}'),
        StringStruct('FileVersion', '{VERSION}'),
        StringStruct('InternalName', '{APP_NAME}'),
        StringStruct('LegalCopyright', '{COPYRIGHT}'),
        StringStruct('OriginalFilename', '{APP_NAME}.exe'),
        StringStruct('ProductName', '{COMPANY_NAME} {APP_NAME}'),
        StringStruct('ProductVersion', '{VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

    path.write_text(content, encoding="utf-8")


# ============================================================
# RUNTIME HOOK
# ============================================================

def create_runtime_hook(path: Path) -> None:
    content = """import os
import sys

if getattr(sys, "frozen", False):
    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    internal_dir = getattr(sys, "_MEIPASS", os.path.join(app_dir, "_internal"))

    tcl_dir = os.path.join(internal_dir, "_tcl_data")
    tk_dir = os.path.join(internal_dir, "_tk_data")

    if os.path.isdir(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir

    if os.path.isdir(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir

    os.chdir(app_dir)
"""

    path.write_text(content, encoding="utf-8")


# ============================================================
# TCL / TK
# ============================================================

def locate_tcl_tk() -> tuple[Path, Path]:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()

        try:
            tcl_dir = Path(root.tk.eval("info library")).resolve()
            tk_dir = Path(root.tk.eval("set tk_library")).resolve()
        finally:
            root.destroy()

    except Exception as exc:
        raise BuildError(f"Nie mogę ustalić katalogów Tcl/Tk: {exc}") from exc

    required = [
        tcl_dir / "init.tcl",
        tk_dir / "tk.tcl",
        tk_dir / "ttk" / "scrollbar.tcl",
    ]

    missing = [str(path) for path in required if not path.is_file()]

    if missing:
        raise BuildError(
            "Instalacja Tcl/Tk jest niekompletna. Brakuje: " + ", ".join(missing)
        )

    print(f"[+] Tcl: {tcl_dir}")
    print(f"[+] Tk : {tk_dir}")

    return tcl_dir, tk_dir


def copy_tcl_tk_runtime(tcl_dir: Path, tk_dir: Path) -> None:
    print_header("Weryfikacja bibliotek Tcl/Tk")

    internal_dir = OUTPUT_DIR / "_internal"
    tcl_target = internal_dir / "_tcl_data"
    tk_target = internal_dir / "_tk_data"

    internal_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(tcl_dir, tcl_target, dirs_exist_ok=True)
    shutil.copytree(tk_dir, tk_target, dirs_exist_ok=True)

    required = [
        tcl_target / "init.tcl",
        tk_target / "tk.tcl",
        tk_target / "ttk" / "scrollbar.tcl",
    ]

    missing = [str(path) for path in required if not path.is_file()]

    if missing:
        raise BuildError(
            "Po buildzie nadal brakuje plików Tcl/Tk: " + ", ".join(missing)
        )

    print(f"[+] Tcl skopiowany do: {tcl_target}")
    print(f"[+] Tk/Ttk skopiowany do: {tk_target}")


# ============================================================
# STAGING
# ============================================================

def check_ted_prerequisites() -> None:
    """
    Kontrola przed buildem: jeżeli TED jest włączony w config.json, to .env
    z kluczem funkcji MUSI być obecny.

    Bez tego aplikacja wstaje, testuje, ale każdy wynik ląduje w kolejce
    logs/ted_queue zamiast w TED — i nikt tego nie zauważy do czasu, aż
    ktoś zapyta o brakujące rekordy.
    """
    print_header("Kontrola integracji TED")

    config_path = ROOT_DIR / "config.json"
    env_path = ROOT_DIR / ".env"

    ted_enabled = None
    db_type = None

    if config_path.is_file():
        try:
            import json
            config = json.loads(config_path.read_text(encoding="utf-8"))
            integrations = config.get("integrations", {})
            ted_enabled = integrations.get("ted_enabled")
            db_type = integrations.get("ted_db_type")
        except Exception as error:
            print(f"[~] Nie mogę odczytać config.json: {error}")
    else:
        print("[~] Brak config.json — pominę kontrolę spójności TED.")

    print(f"[+] ted_enabled : {ted_enabled}")
    print(f"[+] ted_db_type : {db_type!r}"
          f"{'  (tabele TESTOWE)' if db_type == 'TEST' else ''}"
          f"{'  (PRODUKCJA)' if db_type == '' else ''}")
    print(f"[+] .env        : {'obecny' if env_path.is_file() else 'BRAK'}")

    if ted_enabled and not env_path.is_file():
        raise BuildError(
            "TED jest włączony (integrations.ted_enabled = true), ale brakuje "
            "pliku .env z TED_FUNCTION_KEY.\n"
            "    Bez niego wyniki będą trafiać wyłącznie do kolejki "
            "logs/ted_queue, a nie do TED.\n"
            "    Dodaj .env obok create_exe.py albo wyłącz TED w config.json."
        )

    if env_path.is_file():
        content = env_path.read_text(encoding="utf-8", errors="replace")
        if "TED_FUNCTION_KEY" not in content:
            raise BuildError(
                ".env istnieje, ale nie zawiera TED_FUNCTION_KEY. "
                "Sprawdź nazwę zmiennej."
            )
        # Nie wypisujemy wartości klucza.
        print("[+] .env zawiera TED_FUNCTION_KEY")

    if db_type == "":
        print("[!] Cel zapisu: TABELE PRODUKCYJNE TED.")
        print("[!] Rekordy z tego buildu będą danymi produkcyjnymi.")
    elif db_type == "TEST":
        print("[!] Cel zapisu: tabele TESTOWE TED.")
        print("[!] Jeśli to build produkcyjny, ustaw ted_db_type = \"\".")


def prepare_staging(allow_numbered: bool) -> dict[str, Path]:
    print_header("Przygotowanie plików")

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)

    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    resolved: dict[str, Path] = {}

    for canonical_name in PROJECT_FILES:
        source = resolve_project_file(canonical_name, allow_numbered=allow_numbered)
        assert source is not None

        destination = STAGING_DIR / canonical_name
        shutil.copy2(source, destination)

        resolved[canonical_name] = source
        print(f"[+] {source.name} -> {canonical_name}")

    for canonical_name in EDITABLE_DATA_FILES:
        source = resolve_project_file(
            canonical_name, required=False, allow_numbered=allow_numbered
        )

        if source:
            resolved[canonical_name] = source
            print(f"[+] Dane edytowalne: {source.name}")
        else:
            print(f"[~] Brak {canonical_name} — aplikacja utworzy domyślny "
                  "przy pierwszym uruchomieniu")

    for canonical_name in OPTIONAL_DATA_FILES:
        source = resolve_project_file(
            canonical_name, required=False, allow_numbered=allow_numbered
        )

        if source:
            resolved[canonical_name] = source
            print(f"[+] Dane opcjonalne: {source.name}")
        else:
            print(f"[~] Brak {canonical_name} - pomijam")

    create_version_file(STAGING_DIR / "version_info.txt")
    create_runtime_hook(STAGING_DIR / "runtime_hook_hipot_bose.py")

    return resolved


# ============================================================
# BUILD
# ============================================================

def build_application(tcl_dir: Path, tk_dir: Path, allow_numbered: bool,
                      console: bool) -> None:
    print_header("Budowanie HiPot Bose")

    ensure_package("PyInstaller", "pyinstaller")
    ensure_package("customtkinter", "customtkinter")
    ensure_package("serial", "pyserial")
    ensure_package("dotenv", "python-dotenv")

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)

    work_dir = BUILD_DIR / "pyinstaller"
    spec_dir = BUILD_DIR / "spec"

    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",

        "--onedir",
        "--console" if console else "--windowed",
        "--clean",
        "--noconfirm",
        "--noupx",

        "--name", APP_NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),

        "--version-file", str(STAGING_DIR / "version_info.txt"),
        "--runtime-hook", str(STAGING_DIR / "runtime_hook_hipot_bose.py"),

        "--add-data", f"{tcl_dir};_tcl_data",
        "--add-data", f"{tk_dir};_tk_data",

        "--collect-data", "customtkinter",
    ]

    icon = resolve_icon(allow_numbered)

    if icon:
        command.extend(["--icon", str(icon)])
        print(f"[+] Ikona: {icon.name}")
    else:
        print("[~] Brak ikony - używam domyślnej ikony")

    for module_name in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", module_name])

    command.append("main.py")

    run_command(command, cwd=STAGING_DIR)

    if not EXE_PATH.is_file():
        raise BuildError(f"Nie znaleziono utworzonego EXE: {EXE_PATH}")


# ============================================================
# KOPIOWANIE PLIKÓW EDYTOWALNYCH
# ============================================================

def copy_editable_files(resolved: dict[str, Path]) -> None:
    print_header("Kopiowanie konfiguracji")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for canonical_name in EDITABLE_DATA_FILES + OPTIONAL_DATA_FILES:
        source = resolved.get(canonical_name)

        if not source:
            continue

        destination = OUTPUT_DIR / canonical_name
        shutil.copy2(source, destination)

        print(f"[+] {canonical_name} obok EXE")

    logs_dir = OUTPUT_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "ted_queue").mkdir(exist_ok=True)

    print(f"[+] Folder logów: {logs_dir}")


# ============================================================
# MANIFEST
# ============================================================

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest().upper()


def iter_output_files() -> Iterable[Path]:
    for path in sorted(OUTPUT_DIR.rglob("*")):
        if path.is_file() and path.name != "build_manifest.txt":
            yield path


def write_manifest(resolved: dict[str, Path]) -> None:
    manifest = OUTPUT_DIR / "build_manifest.txt"

    lines = [
        f"Application: {APP_NAME}",
        f"Version: {VERSION}",
        f"Company: {COMPANY_NAME}",
        f"Build time: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "Authenticode signed: NO",
        "",
        "Pliki źródłowe użyte do buildu:",
    ]

    for canonical, source in sorted(resolved.items()):
        try:
            checksum = sha256_file(source)
        except OSError:
            checksum = "?" * 64
        lines.append(f"  {canonical:<24} <- {source.name}  [{checksum[:16]}...]")

    lines += ["", "SHA-256 plików wynikowych:"]

    for path in iter_output_files():
        lines.append(f"{sha256_file(path)}  {path.relative_to(OUTPUT_DIR)}")

    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# PODSUMOWANIE
# ============================================================

def show_summary(console: bool) -> None:
    exe_size = EXE_PATH.stat().st_size / (1024 * 1024)

    folder_size = sum(
        path.stat().st_size
        for path in OUTPUT_DIR.rglob("*")
        if path.is_file()
    ) / (1024 * 1024)

    print_header("BUILD ZAKOŃCZONY")

    print(f"Folder aplikacji : {OUTPUT_DIR}")
    print(f"Uruchamiaj       : {EXE_PATH}")
    print(f"Rozmiar EXE      : {exe_size:.1f} MB")
    print(f"Rozmiar folderu  : {folder_size:.1f} MB")
    print(f"Tryb konsoli     : {'TAK (diagnostyczny)' if console else 'NIE'}")
    print("Podpis cyfrowy   : NIE")

    print()
    print("[!] Kopiuj cały folder 'HiPot Bose', nie samo EXE.")
    print("[!] config.json musi pozostać obok EXE.")
    print("[!] logs/ zawiera app.log, config_audit.log i wyniki CSV.")
    print("[!] Po wdrożeniu: zmień hasło inżynieryjne "
          "(Panel → Bezpieczeństwo).")

    env_copied = (OUTPUT_DIR / ".env").is_file()

    if env_copied:
        print("[!] .env z kluczem TED leży obok EXE — NIE udostępniaj tego")
        print("    folderu osobom spoza stanowiska i nie wrzucaj go do repo.")
    else:
        print("[!] .env NIE został skopiowany — TED nie będzie działać,")
        print("    wyniki trafią do kolejki logs/ted_queue.")


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Builder EXE — HiPot Bose")
    parser.add_argument(
        "--allow-numbered", action="store_true",
        help="Akceptuj pliki typu main(2).py (tylko development)",
    )
    parser.add_argument(
        "--console", action="store_true",
        help="Build z widoczną konsolą (diagnostyka)",
    )
    args = parser.parse_args()

    try:
        if os.name != "nt":
            raise BuildError("Builder należy uruchomić na Windows.")

        check_ted_prerequisites()

        resolved = prepare_staging(args.allow_numbered)

        tcl_dir, tk_dir = locate_tcl_tk()

        build_application(tcl_dir, tk_dir, args.allow_numbered, args.console)

        copy_tcl_tk_runtime(tcl_dir, tk_dir)

        copy_editable_files(resolved)

        write_manifest(resolved)

        show_summary(args.console)

        return 0

    except KeyboardInterrupt:
        print("\n[!] Anulowano przez użytkownika.")
        return 130

    except BuildError as error:
        print_header("BŁĄD BUDOWANIA")
        print(f"[!] {error}")
        print(f"[~] Logi/ostrzeżenia PyInstaller: {BUILD_DIR / 'pyinstaller'}")
        return 1

    except Exception as error:
        print_header("NIEOCZEKIWANY BŁĄD")
        print(f"[!] {type(error).__name__}: {error}")
        return 1

    finally:
        if sys.stdin.isatty():
            try:
                input("\nNaciśnij Enter, aby zamknąć...")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
