"""
generate_doc_screenshots.py
---------------------------
Generator screenshotów do dokumentacji aplikacji HiPot Bose.

Nie łączy się z testerem.
Nie wysyła danych do TED.
Nie zapisuje wyniku jako realny test.

Tworzy przykładowe PNG z gotowego ekranu aplikacji.
"""

import time
from pathlib import Path

import customtkinter as ctk
from PIL import ImageGrab

from config import COLORS
from main_screen import MainScreen


OUTPUT_DIR = Path("documentation_screenshots")


def capture_window(window, filename: str):
    """
    Robi screenshot aktywnego okna CTk/Tk i zapisuje jako PNG.
    """
    window.update()
    time.sleep(0.5)

    x = window.winfo_rootx()
    y = window.winfo_rooty()
    w = window.winfo_width()
    h = window.winfo_height()

    image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    OUTPUT_DIR.mkdir(exist_ok=True)
    image.save(OUTPUT_DIR / filename)

    print(f"Zapisano: {OUTPUT_DIR / filename}")


def build_demo_screen(root) -> MainScreen:
    """
    Tworzy ekran MainScreen z przykładowym operatorem.
    """
    screen = MainScreen(
        root,
        hrid="12101333",
        user={
            "name": "Kacper Urbanowicz",
            "role": "operator",
        },
        on_logout=lambda: None,
    )
    screen.place(relwidth=1.0, relheight=1.0)
    return screen


def prepare_common_state(screen: MainScreen, sn: str):
    """
    Ustawia przykładowy SN i profil.
    """
    screen.sn_entry.delete(0, "end")
    screen.sn_entry.insert(0, sn)
    screen._on_sn_change()

    screen.test_btn.configure(state="normal", text="▶ START TEST")
    screen.abort_btn.configure(state="disabled", text="⏹ ABORT")


def make_pass_screenshot():
    root = ctk.CTk()
    root.title("HiPot Bose — Documentation Screenshot")
    root.geometry("820x720")
    root.resizable(False, False)
    root.configure(fg_color=COLORS["bg"])

    screen = build_demo_screen(root)

    sn = "050546123456"
    prepare_common_state(screen, sn)

    hipot_result = {
        "result": "Pass",
        "status": "pass",
        "voltage": "1.50",
        "current": "0.54",
        "time": "2.0",
        "error_desc": "",
    }

    gnd_result = {
        "result": "Pass",
        "status": "pass",
        "resistance": "65.00",
        "current": "25.00",
        "time": "1.0",
        "error_desc": "",
    }

    ted_status = {
        "ok": False,
        "skipped": True,
        "error": "",
        "message": "TED disabled in config",
    }

    screen._show_result(
        sn=sn,
        r=hipot_result,
        gnd=gnd_result,
        ted_status=ted_status,
        csv_path="logs/hipot_log_example.csv",
    )

    root.update()
    capture_window(root, "hipot_result_pass.png")
    root.destroy()


def make_fail_screenshot():
    root = ctk.CTk()
    root.title("HiPot Bose — Documentation Screenshot")
    root.geometry("820x720")
    root.resizable(False, False)
    root.configure(fg_color=COLORS["bg"])

    screen = build_demo_screen(root)

    sn = "074113123456"
    prepare_common_state(screen, sn)

    hipot_result = {
        "result": "Fail",
        "status": "fail",
        "voltage": "3.00",
        "current": "5.20",
        "time": "2.0",
        "error_code": "0001",
        "error_desc": "HI limit przekroczony — prąd za wysoki",
    }

    gnd_result = None

    ted_status = {
        "ok": False,
        "skipped": True,
        "error": "",
        "message": "TED disabled in config",
    }

    screen._show_result(
        sn=sn,
        r=hipot_result,
        gnd=gnd_result,
        ted_status=ted_status,
        csv_path="logs/hipot_log_example.csv",
    )

    root.update()
    capture_window(root, "hipot_result_fail.png")
    root.destroy()


def make_ready_screenshot():
    root = ctk.CTk()
    root.title("HiPot Bose — Documentation Screenshot")
    root.geometry("820x720")
    root.resizable(False, False)
    root.configure(fg_color=COLORS["bg"])

    screen = build_demo_screen(root)

    sn = "050546123456"
    prepare_common_state(screen, sn)

    screen._set_status("Gotowy do testu", COLORS["muted"])

    root.update()
    capture_window(root, "hipot_ready_screen.png")
    root.destroy()


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    make_ready_screenshot()
    make_pass_screenshot()
    make_fail_screenshot()

    print()
    print("Gotowe. Screenshoty są w folderze:")
    print(OUTPUT_DIR.resolve())


if __name__ == "__main__":
    main()