import customtkinter as ctk
from config import COLORS, load_config

class LoginScreen(ctk.CTkFrame):
    def __init__(self, parent, on_login_success):
        super().__init__(parent, fg_color=COLORS["bg"])
        self.on_login_success = on_login_success
        self.config = load_config()
        self._build()

    def _build(self):
        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["card"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
            width=380
        )
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="⚡",
            font=ctk.CTkFont(size=52)
        ).grid(row=0, column=0, pady=(40, 0))

        ctk.CTkLabel(
            card, text="HiPot Tester",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=1, column=0, pady=(6, 2))

        ctk.CTkLabel(
            card, text="Slaughter 4320  •  Bose Production",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"]
        ).grid(row=2, column=0, pady=(0, 28))

        ctk.CTkFrame(
            card, height=1,
            fg_color=COLORS["border"]
        ).grid(row=3, column=0, sticky="ew", padx=32)

        ctk.CTkLabel(
            card, text="HRID operatora",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"], anchor="w"
        ).grid(row=4, column=0, sticky="w", padx=36, pady=(24, 4))

        self.hrid_entry = ctk.CTkEntry(
            card,
            placeholder_text="Wpisz lub zeskanuj HRID...",
            font=ctk.CTkFont(size=14),
            height=44, width=308,
            corner_radius=8,
            border_color=COLORS["border"],
        )
        self.hrid_entry.grid(row=5, column=0, padx=36)
        self.hrid_entry.bind("<Return>", lambda e: self._do_login())
        self.after(100, self.hrid_entry.focus_force)

        self.error_label = ctk.CTkLabel(
            card, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["fail"]
        )
        self.error_label.grid(row=6, column=0, pady=(6, 0))

        ctk.CTkButton(
            card,
            text="Zaloguj się",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=44, width=308,
            corner_radius=8,
            fg_color=COLORS["primary"],
            hover_color="#005a9e",
            command=self._do_login
        ).grid(row=7, column=0, padx=36, pady=(12, 40))

    def _do_login(self):
        hrid = self.hrid_entry.get().strip()
        if not hrid:
            self._show_error("Wprowadź HRID.")
            return
        self.config = load_config()
        user = self.config.get("users", {}).get(hrid)
        if user:
            self.error_label.configure(text="")
            self.on_login_success(hrid, user)
        else:
            self._show_error(f"Nieznany HRID: {hrid}")
            self.hrid_entry.delete(0, "end")
            self.after(100, self.hrid_entry.focus_force)

    def _show_error(self, msg: str):
        self.error_label.configure(text=f"⚠  {msg}")