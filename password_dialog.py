import customtkinter as ctk
from config import COLORS  # zamiast lokalnego słownika COLORS

ENG_PASSWORD = "bose2024"

class PasswordDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_success):
        super().__init__(parent)
        self.on_success = on_success
        self.title("")
        self.geometry("340x280")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self.grab_set()        # blokuje główne okno
        self.focus_force()
        self._build()

    def _build(self):
        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["card"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"]
        )
        card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.88)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="🔒  Dostęp zastrzeżony",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, pady=(24, 4))

        ctk.CTkLabel(
            card,
            text="Podaj hasło inżynieryjne",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"]
        ).grid(row=1, column=0, pady=(0, 16))

        self.pass_entry = ctk.CTkEntry(
            card,
            placeholder_text="Hasło...",
            font=ctk.CTkFont(size=14),
            height=40,
            width=240,
            corner_radius=8,
            border_color=COLORS["border"],
            show="•"           # maskowanie
        )
        self.pass_entry.grid(row=2, column=0, padx=24)
        self.pass_entry.bind("<Return>", lambda e: self._check())
        self.pass_entry.focus()

        self.error_label = ctk.CTkLabel(
            card, text="",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["fail"]
        )
        self.error_label.grid(row=3, column=0, pady=(4, 0))

        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.grid(row=4, column=0, pady=(12, 24))

        ctk.CTkButton(
            btn_frame,
            text="Anuluj",
            width=100,
            height=36,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self.destroy
        ).grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            btn_frame,
            text="Wejdź",
            width=100,
            height=36,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["primary"],
            hover_color="#005a9e",
            command=self._check
        ).grid(row=0, column=1)

    def _check(self):
        entered = self.pass_entry.get()
        if entered == ENG_PASSWORD:
            self.destroy()
            self.on_success()
        else:
            self.error_label.configure(text="⚠  Nieprawidłowe hasło")
            self.pass_entry.delete(0, "end")
            self.pass_entry.focus()