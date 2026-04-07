import json
import os
import customtkinter as ctk
from engineer_panel import EngineerPanel  # ZMIANA — import z osobnego pliku

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "bg":      "#1a1a1a",
    "card":    "#2d2d2d",
    "border":  "#3a3a3a",
    "primary": "#0078d4",
    "fail":    "#d13438",
    "text":    "#ffffff",
    "muted":   "#9d9d9d",
}

CONFIG_FILE = "config.json"

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        default = {"users": {}}
        save_config(default)
        return default
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


class LoginScreen(ctk.CTkFrame):
    def __init__(self, parent, on_login_success, on_engineer_panel):
        super().__init__(parent, fg_color=COLORS["bg"])
        self.on_login_success = on_login_success
        self.on_engineer_panel = on_engineer_panel
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
            text_color=COLORS["muted"],
            anchor="w"
        ).grid(row=4, column=0, sticky="w", padx=36, pady=(24, 4))

        self.hrid_entry = ctk.CTkEntry(
            card,
            placeholder_text="Wpisz lub zeskanuj HRID...",
            font=ctk.CTkFont(size=14),
            height=44,
            width=308,
            corner_radius=8,
            border_color=COLORS["border"],
        )
        self.hrid_entry.grid(row=5, column=0, padx=36)
        self.hrid_entry.bind("<Return>", lambda e: self._do_login())
        self.hrid_entry.focus()

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
            height=44,
            width=308,
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
            self.hrid_entry.focus()

    def _show_error(self, msg: str):
        self.error_label.configure(text=f"⚠  {msg}")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("HiPot Tester — Bose Production")
        self.geometry("520x480")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])

        # ZMIANA — licznik kliknięć litery D + reset timer
        self._d_count = 0
        self._d_reset_job = None

        self._show_login()

    def _show_login(self):
        for w in self.winfo_children():
            w.destroy()
        screen = LoginScreen(
            self,
            on_login_success=self._on_login,
            on_engineer_panel=self._open_engineer_panel
        )
        screen.place(relwidth=1.0, relheight=1.0)

    def _on_login(self, hrid: str, user: dict):
        # Dopiero po zalogowaniu bindujemy skrót  # ZMIANA
        self.bind("<Control-Shift-Alt-KeyPress-d>", self._on_d_press)
        print(f"Zalogowano: {hrid} | {user['name']} | {user['role']}")
        # Następny krok: self._show_test_screen(hrid, user)

    # ZMIANA — logika 3x kliknięcia D w ciągu 1 sekundy ───────────────────
    def _on_d_press(self, event=None):
        self._d_count += 1

        # Reset timera przy każdym naciśnięciu
        if self._d_reset_job:
            self.after_cancel(self._d_reset_job)
        self._d_reset_job = self.after(1000, self._reset_d_count)

        if self._d_count >= 3:
            self._d_count = 0
            self._open_engineer_panel()

    def _reset_d_count(self):
        self._d_count = 0

    def _open_engineer_panel(self):
        EngineerPanel(self)


if __name__ == "__main__":
    app = App()
    app.mainloop()