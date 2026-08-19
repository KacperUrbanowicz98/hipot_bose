"""
login_screen.py
---------------
Logowanie operatora numerem HRID.

Zmiany:
  - konfiguracja czytana bezpiecznie (uszkodzony config.json nie wywraca
    już ekranu logowania),
  - czytelny komunikat, gdy w config.json nie ma ANI JEDNEGO użytkownika —
    wcześniej wyglądało to jak "zły HRID", a przyczyną był pusty plik,
  - logowania trafiają do logu audytowego.

Uwaga procesowa: logowanie odbywa się wyłącznie na HRID, bez hasła. Kto zna
cudzy numer, testuje jako ta osoba — pole operator w CSV i Username w TED
nie są w tym modelu dowodem tożsamości. To świadome ograniczenie stanowiska
produkcyjnego ze skanerem, ale warto je mieć spisane w dokumentacji procesu.
"""

from __future__ import annotations

import customtkinter as ctk

from app_logging import audit, get_logger
from config import COLORS, load_config

log = get_logger(__name__)


class LoginScreen(ctk.CTkFrame):
    def __init__(self, parent, on_login_success):
        super().__init__(parent, fg_color=COLORS["bg"])
        self.on_login_success = on_login_success
        self.config_data = load_config()
        self._build()

    def _build(self):
        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["card"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
            width=380,
        )
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="⚡", font=ctk.CTkFont(size=52)).grid(
            row=0, column=0, pady=(40, 0)
        )

        ctk.CTkLabel(
            card, text="HiPot Tester",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=1, column=0, pady=(6, 2))

        ctk.CTkLabel(
            card, text="Slaughter 4320  •  Bose Production",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=2, column=0, pady=(0, 28))

        ctk.CTkFrame(card, height=1, fg_color=COLORS["border"]).grid(
            row=3, column=0, sticky="ew", padx=32
        )

        ctk.CTkLabel(
            card, text="HRID operatora",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"], anchor="w",
        ).grid(row=4, column=0, sticky="w", padx=36, pady=(24, 4))

        self.hrid_entry = ctk.CTkEntry(
            card,
            placeholder_text="Wpisz lub zeskanuj HRID...",
            font=ctk.CTkFont(size=14),
            height=44, width=308, corner_radius=8,
            border_color=COLORS["border"],
        )
        self.hrid_entry.grid(row=5, column=0, padx=36)
        self.hrid_entry.bind("<Return>", lambda e: self._do_login())
        self.after(100, self.hrid_entry.focus_force)

        self.error_label = ctk.CTkLabel(
            card, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["fail"],
            wraplength=300,
        )
        self.error_label.grid(row=6, column=0, pady=(6, 0), padx=20)

        ctk.CTkButton(
            card,
            text="Zaloguj się",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=44, width=308, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._do_login,
        ).grid(row=7, column=0, padx=36, pady=(12, 40))

    def _do_login(self):
        hrid = self.hrid_entry.get().strip()

        if not hrid:
            self._show_error("Wprowadź HRID.")
            return

        self.config_data = load_config()
        users = self.config_data.get("users", {})

        if not users:
            self._show_error(
                "Brak zdefiniowanych użytkowników w config.json. "
                "Skontaktuj się z inżynierem."
            )
            log.error("Logowanie niemożliwe — pusta lista użytkowników.")
            return

        user = users.get(hrid)

        if user:
            self.error_label.configure(text="")
            log.info("Zalogowano: %s (%s)", hrid, user.get("name", ""))
            integrations = self.config_data.get("integrations", {})
            db_type = str(integrations.get("ted_db_type", "")).strip()
            target = "PRODUKCJA" if not db_type else db_type

            audit(
                f"{hrid} {user.get('name', '')}".strip(),
                "LOGIN",
                f"TED={'ON' if integrations.get('ted_enabled') else 'OFF'} "
                f"cel={target}",
            )
            self.on_login_success(hrid, user)
        else:
            self._show_error(f"Nieznany HRID: {hrid}")
            log.warning("Nieudane logowanie, HRID=%s", hrid)
            self.hrid_entry.delete(0, "end")
            self.after(100, self.hrid_entry.focus_force)

    def _show_error(self, msg: str):
        self.error_label.configure(text=f"⚠  {msg}")
