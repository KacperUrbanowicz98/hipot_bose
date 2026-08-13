"""
password_dialog.py
------------------
Dostęp do Panelu Inżynieryjnego.

Zmiany:
  - hasło NIE jest już stringiem w kodzie. Wcześniej ENG_PASSWORD = "bose2024"
    było jawnie w źródle i w zbudowanym EXE — do wyciągnięcia narzędziem
    `strings` w kilka sekund, a chroniło parametry mające znaczenie dla
    bezpieczeństwa (napięcie testu, limity, prąd Ground Bond).
    Teraz w config.json leży wyłącznie hash PBKDF2-HMAC-SHA256.

  - lockout po serii błędnych prób (domyślnie 5 prób -> 60 s blokady),
    licznik trzymany na poziomie modułu, więc zamknięcie i ponowne otwarcie
    okna go nie zeruje.

  - wszystkie próby wejścia trafiają do logu audytowego.

Migracja: przy pierwszym uruchomieniu config.py wpisuje do config.json hash
dotychczasowego hasła i podnosi flagę must_change_password, żeby wdrożenie
nie odcięło nikogo od panelu. Zmiana hasła: Panel Inżynieryjny → Bezpieczeństwo.
"""

from __future__ import annotations

import time

import customtkinter as ctk

from app_logging import audit, get_logger
from config import COLORS, get_section, load_config, verify_password

log = get_logger(__name__)


# ── Stan lockoutu wspólny dla wszystkich instancji okna ────────────────────
_failed_attempts = 0
_locked_until = 0.0


def _lock_remaining() -> int:
    return max(0, int(_locked_until - time.time()))


def _register_failure(max_attempts: int, lockout_seconds: int) -> int:
    """Zwraca liczbę pozostałych prób (0 = właśnie nastąpiła blokada)."""
    global _failed_attempts, _locked_until

    _failed_attempts += 1

    if _failed_attempts >= max_attempts:
        _locked_until = time.time() + lockout_seconds
        _failed_attempts = 0
        return 0

    return max_attempts - _failed_attempts


def _register_success():
    global _failed_attempts, _locked_until
    _failed_attempts = 0
    _locked_until = 0.0


class PasswordDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_success, actor: str = ""):
        super().__init__(parent)
        self.on_success = on_success
        self.actor = actor or "UNKNOWN"

        self.title("")
        self.geometry("360x320")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self.grab_set()
        self.focus_force()

        security = get_section("security")
        self.max_attempts = int(security.get("max_password_attempts", 5))
        self.lockout_seconds = int(security.get("lockout_seconds", 60))

        self._build()
        self._refresh_lock_state()

    def _build(self):
        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["card"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.88)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="🔒  Dostęp zastrzeżony",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, pady=(24, 4))

        ctk.CTkLabel(
            card,
            text="Podaj hasło inżynieryjne",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=1, column=0, pady=(0, 16))

        self.pass_entry = ctk.CTkEntry(
            card,
            placeholder_text="Hasło...",
            font=ctk.CTkFont(size=14),
            height=40, width=240, corner_radius=8,
            border_color=COLORS["border"],
            show="•",
        )
        self.pass_entry.grid(row=2, column=0, padx=24)
        self.pass_entry.bind("<Return>", lambda e: self._check())
        self.pass_entry.focus()

        self.error_label = ctk.CTkLabel(
            card, text="",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["fail"],
            wraplength=260,
        )
        self.error_label.grid(row=3, column=0, pady=(6, 0), padx=16)

        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.grid(row=4, column=0, pady=(12, 24))

        ctk.CTkButton(
            btn_frame,
            text="Anuluj",
            width=100, height=36,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self.destroy,
        ).grid(row=0, column=0, padx=(0, 8))

        self.enter_btn = ctk.CTkButton(
            btn_frame,
            text="Wejdź",
            width=100, height=36,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["primary"],
            hover_color="#005a9e",
            command=self._check,
        )
        self.enter_btn.grid(row=0, column=1)

    def _refresh_lock_state(self):
        """Odlicza blokadę i trzyma przycisk wyłączony, dopóki trwa."""
        remaining = _lock_remaining()

        if remaining > 0:
            self.enter_btn.configure(state="disabled")
            self.pass_entry.configure(state="disabled")
            self.error_label.configure(
                text=f"⛔ Zbyt wiele błędnych prób. Odblokowanie za {remaining} s.",
                text_color=COLORS["fail"],
            )
            self.after(1000, self._refresh_lock_state)
        else:
            self.enter_btn.configure(state="normal")
            self.pass_entry.configure(state="normal")

    def _check(self):
        if _lock_remaining() > 0:
            return

        entered = self.pass_entry.get()

        if not entered:
            self.error_label.configure(
                text="⚠  Wprowadź hasło", text_color=COLORS["warning"]
            )
            return

        config = load_config()

        if verify_password(entered, config):
            _register_success()
            audit(self.actor, "ENGINEER_LOGIN", "dostęp przyznany")
            log.info("Panel inżynieryjny: dostęp przyznany (%s)", self.actor)

            self.destroy()
            self.on_success()
            return

        left = _register_failure(self.max_attempts, self.lockout_seconds)

        audit(self.actor, "ENGINEER_LOGIN_FAILED",
              f"pozostałe próby: {left}")
        log.warning("Panel inżynieryjny: błędne hasło (%s), pozostałe próby: %d",
                    self.actor, left)

        self.pass_entry.delete(0, "end")

        if left == 0:
            self._refresh_lock_state()
        else:
            self.error_label.configure(
                text=f"⚠  Nieprawidłowe hasło (pozostałe próby: {left})",
                text_color=COLORS["fail"],
            )
            self.pass_entry.focus()
