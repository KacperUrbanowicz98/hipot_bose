"""
create_exe.py
-------------
Builder EXE dla aplikacji HiPot Bose.

Buduje aplikację PyInstallerem w trybie ONEDIR.

Założenia:
    - główny plik: main.py
    - config.json zostaje obok EXE i może być edytowany po buildzie
    - TED jest obecnie wyłączony przez config:
        "integrations": {
            "ted_enabled": false,
            "ted_db_type": "TEST"
        }
    - .env NIE jest kopiowany domyślnie do release, bo TED jest wstrzymany
    - pliki testowe/diagnostyczne nie są pakowane do aplikacji

Uruchomienie:
    python create_exe.py

Wynik:
    dist/HiPot Bose/HiPot Bose.exe
"""

from __future__ import annotations

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
VERSION = "1.0.0.0"
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
# Nie dodajemy tutaj plików diagnostycznych:
#   ground_bond_test.py
#   relay_test.py
#   hipot_test_connection.py
#   bezRTS.py
#   niedzialajce3rzeczy.py
#   test_ted_send.py

PROJECT_FILES = [
    "main.py",
    "config.py",
    "login_screen.py",
    "main_screen.py",
    "hipot_controller.py",
    "relay_controller.py",
    "result_logger.py",
    "engineer_panel.py",
    "password_dialog.py",
    "ted_client.py",
]

# Pliki edytowalne, które mają zostać obok EXE.
EDITABLE_DATA_FILES = [
    "config.json",
]

# Pliki opcjonalne. Domyślnie .env nie kopiujemy do release,
# bo TED jest wstrzymany. Gdy IT potwierdzi TED, można dostarczyć .env osobno.
OPTIONAL_DATA_FILES = [
    # ".env",
]

# Opcjonalne ikony — builder wybierze pierwszą znalezioną.
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

    # Opcjonalne .env dla TED
    "dotenv",

    # Moduły aplikacji
    "config",
    "login_screen",
    "main_screen",
    "hipot_controller",
    "relay_controller",
    "result_logger",
    "engineer_panel",
    "password_dialog",
    "ted_client",
]


# ============================================================
# WYJĄTEK BUILDERA
# ============================================================

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
    """
    Sprawdza, czy pakiet jest dostępny.
    Jeśli nie, instaluje go przez pip.
    """
    if importlib.util.find_spec(import_name) is not None:
        return

    print(f"[~] Brak pakietu {pip_name}. Instaluję...")
    run_command([sys.executable, "-m", "pip", "install", pip_name])


def numeric_suffix(path: Path, canonical_name: str) -> int:
    """
    Pomaga wybrać najnowszy plik typu:
        main.py
        main(1).py
        main(2).py

    Preferuje nazwę kanoniczną bez numeru.
    """
    if path.name.lower() == canonical_name.lower():
        return 10**9

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
) -> Optional[Path]:
    """
    Szuka pliku projektu.

    Obsługuje też nazwy pobrane z ChatGPT / przeglądarki, np.:
        main.py
        main(1).py
        main(5).py
    """
    exact = ROOT_DIR / canonical_name

    if exact.is_file():
        return exact

    canonical = Path(canonical_name)
    pattern = re.compile(
        rf"^{re.escape(canonical.stem)}(?:\((\d+)\))?{re.escape(canonical.suffix)}$",
        re.IGNORECASE,
    )

    candidates = [
        path
        for path in ROOT_DIR.iterdir()
        if path.is_file() and pattern.match(path.name)
    ]

    if candidates:
        selected = max(
            candidates,
            key=lambda p: (numeric_suffix(p, canonical_name), p.stat().st_mtime),
        )
        print(f"[~] {canonical_name}: używam {selected.name}")
        return selected

    if required:
        raise BuildError(f"Brak wymaganego pliku: {canonical_name}")

    return None


def resolve_icon() -> Optional[Path]:
    for icon_name in OPTIONAL_ICONS:
        icon = resolve_project_file(icon_name, required=False)
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
    """
    Runtime hook:
        - ustawia katalog pracy na folder EXE
        - ustawia ścieżki Tcl/Tk po spakowaniu
    """
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
    """
    Znajduje katalogi Tcl/Tk używane przez obecnego Pythona.
    Potrzebne dla tkinter/customtkinter.
    """
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
            "Instalacja Tcl/Tk jest niekompletna. Brakuje: "
            + ", ".join(missing)
        )

    print(f"[+] Tcl: {tcl_dir}")
    print(f"[+] Tk : {tk_dir}")

    return tcl_dir, tk_dir


def copy_tcl_tk_runtime(tcl_dir: Path, tk_dir: Path) -> None:
    """
    Po buildzie kopiuje pełne Tcl/Tk do _internal.
    """
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
            "Po buildzie nadal brakuje plików Tcl/Tk: "
            + ", ".join(missing)
        )

    print(f"[+] Tcl skopiowany do: {tcl_target}")
    print(f"[+] Tk/Ttk skopiowany do: {tk_target}")


# ============================================================
# STAGING
# ============================================================

def prepare_staging() -> dict[str, Path]:
    print_header("Przygotowanie plików")

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)

    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    resolved: dict[str, Path] = {}

    for canonical_name in PROJECT_FILES:
        source = resolve_project_file(canonical_name)
        assert source is not None

        destination = STAGING_DIR / canonical_name
        shutil.copy2(source, destination)

        resolved[canonical_name] = source
        print(f"[+] {source.name} -> {canonical_name}")

    for canonical_name in EDITABLE_DATA_FILES:
        source = resolve_project_file(canonical_name)
        assert source is not None

        resolved[canonical_name] = source
        print(f"[+] Dane edytowalne: {source.name}")

    for canonical_name in OPTIONAL_DATA_FILES:
        source = resolve_project_file(canonical_name, required=False)

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

def build_application(tcl_dir: Path, tk_dir: Path) -> None:
    print_header("Budowanie HiPot Bose")

    ensure_package("PyInstaller", "pyinstaller")
    ensure_package("customtkinter", "customtkinter")
    ensure_package("serial", "pyserial")

    # TED jest wyłączony, ale ted_client.py opcjonalnie importuje dotenv.
    # Instalujemy, żeby później nie było problemu, gdy TED zostanie włączony.
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
        "--windowed",
        "--clean",
        "--noconfirm",
        "--noupx",

        "--name",
        APP_NAME,

        "--distpath",
        str(DIST_DIR),

        "--workpath",
        str(work_dir),

        "--specpath",
        str(spec_dir),

        "--version-file",
        str(STAGING_DIR / "version_info.txt"),

        "--runtime-hook",
        str(STAGING_DIR / "runtime_hook_hipot_bose.py"),

        # Tcl/Tk
        "--add-data",
        f"{tcl_dir};_tcl_data",

        "--add-data",
        f"{tk_dir};_tk_data",

        # CustomTkinter ma własne assety/theme.
        "--collect-data",
        "customtkinter",
    ]

    icon = resolve_icon()

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


def write_manifest() -> None:
    manifest = OUTPUT_DIR / "build_manifest.txt"

    lines = [
        f"Application: {APP_NAME}",
        f"Version: {VERSION}",
        f"Company: {COMPANY_NAME}",
        f"Build time: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "Authenticode signed: NO",
        "",
        "SHA-256:",
    ]

    for path in iter_output_files():
        lines.append(f"{sha256_file(path)}  {path.relative_to(OUTPUT_DIR)}")

    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# PODSUMOWANIE
# ============================================================

def show_summary() -> None:
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
    print("Podpis cyfrowy   : NIE")

    print()
    print("[!] Kopiuj cały folder 'HiPot Bose', nie samo EXE.")
    print("[!] config.json musi pozostać obok EXE.")
    print("[!] logs/ będzie zawierał lokalne pliki CSV.")
    print("[!] .env nie jest kopiowany, bo TED jest aktualnie wstrzymany.")


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    try:
        if os.name != "nt":
            raise BuildError("Builder należy uruchomić na Windows.")

        resolved = prepare_staging()

        tcl_dir, tk_dir = locate_tcl_tk()

        build_application(tcl_dir, tk_dir)

        copy_tcl_tk_runtime(tcl_dir, tk_dir)

        copy_editable_files(resolved)

        write_manifest()

        show_summary()

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