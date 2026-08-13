"""
main.py
-------
Punkt wejścia aplikacji HiPot Bose.

Zmiany:
  - inicjalizacja logowania do pliku PRZED zbudowaniem UI (build --windowed
    nie ma konsoli, więc print() nic nie dawał),
  - HRID zalogowanej osoby jest przekazywany do panelu inżynieryjnego,
    żeby log audytowy wiedział, KTO zmienił parametry,
  - blokada wejścia do panelu inżynieryjnego w trakcie testu,
  - globalny handler wyjątków Tk — wcześniej wyjątek w callbacku szedł
    na nieistniejący stderr i znikał.
"""

from __future__ import annotations

import sys
import traceback
from tkinter import messagebox

import customtkinter as ctk

import runtime_state
from app_logging import get_logger, setup_logging
from config import COLORS, must_change_password
from engineer_panel import EngineerPanel
from login_screen import LoginScreen
from main_screen import MainScreen
from password_dialog import PasswordDialog

setup_logging()
log = get_logger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("HiPot Tester — Bose Production")
        self.geometry("820x760")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])

        self._d_count = 0
        self._d_reset_job = None

        self._hrid = ""
        self._user = {}

        # Wyjątki z callbacków Tk trafiają do logu, a nie w próżnię.
        self.report_callback_exception = self._on_tk_exception

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._show_login()

    # ── Obsługa wyjątków Tk ────────────────────────────────────────────────
    def _on_tk_exception(self, exc_type, exc_value, exc_traceback):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        log.error("Nieobsłużony wyjątek w UI:\n%s", text)

        try:
            messagebox.showerror(
                "Błąd aplikacji",
                f"{exc_type.__name__}: {exc_value}\n\n"
                "Szczegóły zapisano w logs/app.log.",
                parent=self,
            )
        except Exception:
            pass

    def _on_close(self):
        if runtime_state.test_in_progress():
            messagebox.showwarning(
                "Test w toku",
                "Trwa test — zamknięcie aplikacji jest zablokowane.\n"
                "Poczekaj na zakończenie albo użyj przycisku ABORT.",
                parent=self,
            )
            return

        log.info("Zamknięcie aplikacji.")
        self.destroy()

    # ── Ekrany ─────────────────────────────────────────────────────────────
    def _show_login(self):
        for w in self.winfo_children():
            w.destroy()

        LoginScreen(self, on_login_success=self._on_login).place(
            relwidth=1.0, relheight=1.0
        )

    def _on_login(self, hrid: str, user: dict):
        self._hrid = hrid
        self._user = user

        self.bind("<KeyPress>", self._on_key_press)

        for w in self.winfo_children():
            w.destroy()

        MainScreen(
            self, hrid=hrid, user=user, on_logout=self._on_logout
        ).place(relwidth=1.0, relheight=1.0)

        if must_change_password():
            self.after(700, self._warn_default_password)

    def _warn_default_password(self):
        log.warning("Hasło inżynieryjne nie zostało jeszcze zmienione.")

    def _on_logout(self):
        if runtime_state.test_in_progress():
            return

        self.unbind("<KeyPress>")
        self._d_count = 0
        self._hrid = ""
        self._user = {}
        self._show_login()

    # ── Skrót do panelu inżynieryjnego ─────────────────────────────────────
    def _on_key_press(self, event):
        ctrl = (event.state & 0x0004) != 0
        shift = (event.state & 0x0001) != 0
        alt = (event.state & 0x20000) != 0
        key = event.keysym.lower() == "d"

        if not (ctrl and shift and alt and key):
            return

        self._d_count += 1

        if self._d_reset_job:
            self.after_cancel(self._d_reset_job)

        self._d_reset_job = self.after(1000, self._reset_d_count)

        if self._d_count < 3:
            return

        self._d_count = 0
        self._open_engineer_panel()

    def _open_engineer_panel(self):
        # Panel otwiera własne połączenia z testerem i ESP. W trakcie testu
        # groziłoby to przełączeniem styków pod napięciem albo RESET-em
        # w połowie sekwencji.
        if runtime_state.test_in_progress():
            messagebox.showwarning(
                "Test w toku",
                runtime_state.guard_message(),
                parent=self,
            )
            return

        actor = f"{self._hrid} {self._user.get('name', '')}".strip()

        PasswordDialog(
            self,
            on_success=lambda: EngineerPanel(self, actor=actor),
            actor=actor,
        )

    def _reset_d_count(self):
        self._d_count = 0


def main() -> int:
    try:
        app = App()
        app.mainloop()
        return 0

    except Exception as e:
        log.exception("Aplikacja zakończyła się błędem")

        try:
            messagebox.showerror(
                "Błąd krytyczny",
                f"{type(e).__name__}: {e}\n\nSzczegóły w logs/app.log.",
            )
        except Exception:
            pass

        return 1


if __name__ == "__main__":
    sys.exit(main())
