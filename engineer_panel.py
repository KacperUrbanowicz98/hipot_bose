import customtkinter as ctk

COLORS = {
    "bg":      "#1a1a1a",
    "card":    "#2d2d2d",
    "border":  "#3a3a3a",
    "primary": "#0078d4",
    "text":    "#ffffff",
    "muted":   "#9d9d9d",
}

class EngineerPanel(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Panel Inżynieryjny")
        self.geometry("640x480")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self.grab_set()  # Modal
        self._build()

    def _build(self):
        # Nagłówek
        ctk.CTkLabel(
            self,
            text="🔧  Panel Inżynieryjny",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text"]
        ).pack(pady=(28, 16), padx=24, anchor="w")

        # Zakładki
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=COLORS["card"],
            segmented_button_fg_color=COLORS["bg"],
            segmented_button_selected_color=COLORS["primary"],
            segmented_button_selected_hover_color="#005a9e",
            segmented_button_unselected_color=COLORS["bg"],
            segmented_button_unselected_hover_color=COLORS["card"],
        )
        self.tabs.pack(fill="both", expand=True, padx=24, pady=(0, 24))

        self.tabs.add("Zarządzanie HRID")
        self.tabs.add("Konfiguracja portu")
        self.tabs.add("Profile testowe")
        self.tabs.add("Diagnostyka")

        self._build_hrid_tab()
        self._build_port_tab()
        self._build_profiles_tab()
        self._build_diagnostics_tab()

    # ── Zakładka: HRID ─────────────────────────────────────────────────────
    def _build_hrid_tab(self):
        tab = self.tabs.tab("Zarządzanie HRID")
        ctk.CTkLabel(
            tab,
            text="Tu będzie zarządzanie użytkownikami HRID.",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["muted"]
        ).pack(pady=40)

    # ── Zakładka: Port ─────────────────────────────────────────────────────
    def _build_port_tab(self):
        tab = self.tabs.tab("Konfiguracja portu")
        ctk.CTkLabel(
            tab,
            text="Tu będzie konfiguracja portu COM i parametrów RS-232.",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["muted"]
        ).pack(pady=40)

    # ── Zakładka: Profile ──────────────────────────────────────────────────
    def _build_profiles_tab(self):
        tab = self.tabs.tab("Profile testowe")
        ctk.CTkLabel(
            tab,
            text="Tu będzie zarządzanie profilami testowymi.",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["muted"]
        ).pack(pady=40)

    # ── Zakładka: Diagnostyka ──────────────────────────────────────────────
    def _build_diagnostics_tab(self):
        tab = self.tabs.tab("Diagnostyka")
        ctk.CTkLabel(
            tab,
            text="Tu będzie weryfikacja połączenia RS-232.",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["muted"]
        ).pack(pady=40)