import customtkinter as ctk
from config import COLORS, load_config, save_config


class EngineerPanel(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Panel Inżynieryjny")
        self.geometry("740x620")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self.grab_set()
        self._build()

    def _build(self):
        ctk.CTkLabel(
            self,
            text="🔧  Panel Inżynieryjny",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text"]
        ).pack(pady=(24, 12), padx=24, anchor="w")

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

        self.tabs.add("Profile testowe")
        self.tabs.add("Zarządzanie HRID")
        self.tabs.add("Konfiguracja portu")
        self.tabs.add("Diagnostyka")

        self._build_profiles_tab()
        self._build_hrid_tab()
        self._build_port_tab()
        self._build_diagnostics_tab()

    # ── Zakładka: Profile testowe ──────────────────────────────────────────
    def _build_profiles_tab(self):
        tab = self.tabs.tab("Profile testowe")
        tab.grid_columnconfigure(0, weight=1)

        # Nagłówek zakładki
        ctk.CTkLabel(
            tab,
            text="Edycja profilu testowego",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(8, 16))

        # Wczytaj aktualny profil 1
        config  = load_config()
        profile = config.get("profiles", {}).get("1", {})

        # Definicja pól formularza
        fields = [
            ("Nazwa profilu",       "name",      "Standard 3kV",  False),
            ("Typ testu",           "type",      "ACW",           False),
            ("Napięcie (kV)",       "voltage",   "3.00",          False),
            ("HI limit (mA)",       "hi_limit",  "4.0",           False),
            ("LO limit (mA)",       "lo_limit",  "0.5",           False),
            ("Ramp-up (s)",         "ramp",      "1.0",           False),
            ("Dwell (s)",           "dwell",     "2.0",           False),
            ("Częstotliwość",       "frequency", "0",             True ),
        ]

        self._entries   = {}
        self._freq_var  = ctk.StringVar(value="50 Hz (0)")

        for i, (label, key, default, is_freq) in enumerate(fields):
            # Label
            ctk.CTkLabel(
                tab,
                text=label,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["muted"],
                anchor="w",
                width=160
            ).grid(row=i + 1, column=0, sticky="w", padx=(0, 12), pady=4)

            if is_freq:
                # Częstotliwość jako OptionMenu
                val = str(profile.get(key, default))
                self._freq_var.set("50 Hz (0)" if val == "0" else "60 Hz (1)")
                widget = ctk.CTkOptionMenu(
                    tab,
                    values=["50 Hz (0)", "60 Hz (1)"],
                    variable=self._freq_var,
                    fg_color=COLORS["card"],
                    button_color=COLORS["primary"],
                    button_hover_color="#005a9e",
                    font=ctk.CTkFont(size=13),
                    width=280
                )
                widget.grid(row=i + 1, column=1, sticky="w", pady=4)
                self._entries[key] = widget
            else:
                val = str(profile.get(key, default))
                entry = ctk.CTkEntry(
                    tab,
                    font=ctk.CTkFont(size=13),
                    height=36,
                    width=280,
                    corner_radius=8,
                    border_color=COLORS["border"],
                )
                entry.insert(0, val)
                entry.grid(row=i + 1, column=1, sticky="w", pady=4)
                self._entries[key] = entry

        # Pasek statusu
        self._profile_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"]
        )
        self._profile_status.grid(
            row=len(fields) + 1, column=0,
            columnspan=2, pady=(8, 0), sticky="w"
        )

        # Przyciski
        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(
            row=len(fields) + 2, column=0,
            columnspan=2, pady=(8, 0), sticky="w"
        )

        ctk.CTkButton(
            btn_frame,
            text="💾  Zapisz profil",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            width=160,
            corner_radius=8,
            fg_color=COLORS["primary"],
            hover_color="#005a9e",
            command=self._save_profile
        ).grid(row=0, column=0, padx=(0, 12))

        ctk.CTkButton(
            btn_frame,
            text="↺  Resetuj",
            font=ctk.CTkFont(size=13),
            height=38,
            width=120,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._reset_profile
        ).grid(row=0, column=1)

    def _save_profile(self):
        try:
            # Walidacja i parsowanie
            name     = self._entries["name"].get().strip()
            ptype    = self._entries["type"].get().strip().upper()
            voltage  = float(self._entries["voltage"].get().strip())
            hi_limit = float(self._entries["hi_limit"].get().strip())
            lo_limit = float(self._entries["lo_limit"].get().strip())
            ramp     = float(self._entries["ramp"].get().strip())
            dwell    = float(self._entries["dwell"].get().strip())
            freq_str = self._freq_var.get()
            frequency = 0 if "50" in freq_str else 1

            # Walidacja zakresów
            if not (0.1 <= voltage <= 5.0):
                self._show_profile_status("⚠  Napięcie musi być 0.1–5.0 kV", COLORS["fail"])
                return
            if hi_limit <= lo_limit:
                self._show_profile_status("⚠  HI limit musi być większy niż LO limit", COLORS["fail"])
                return
            if ramp < 0.1:
                self._show_profile_status("⚠  Ramp minimum 0.1 s", COLORS["fail"])
                return
            if dwell < 0.2:
                self._show_profile_status("⚠  Dwell minimum 0.2 s", COLORS["fail"])
                return

            # Zapis
            config = load_config()
            if "profiles" not in config:
                config["profiles"] = {}
            config["profiles"]["1"] = {
                "name":      name,
                "type":      ptype,
                "voltage":   voltage,
                "hi_limit":  hi_limit,
                "lo_limit":  lo_limit,
                "ramp":      ramp,
                "dwell":     dwell,
                "frequency": frequency
            }
            save_config(config)
            self._show_profile_status("✔  Profil zapisany pomyślnie", COLORS["success"])

        except ValueError:
            self._show_profile_status("⚠  Nieprawidłowa wartość — sprawdź pola", COLORS["fail"])

    def _reset_profile(self):
        defaults = {
            "name":    "Standard 3kV",
            "type":    "ACW",
            "voltage": "3.00",
            "hi_limit": "4.0",
            "lo_limit": "0.5",
            "ramp":    "1.0",
            "dwell":   "2.0",
        }
        for key, val in defaults.items():
            e = self._entries[key]
            e.delete(0, "end")
            e.insert(0, val)
        self._freq_var.set("50 Hz (0)")
        self._show_profile_status("↺  Przywrócono wartości domyślne", COLORS["muted"])

    def _show_profile_status(self, msg: str, color: str):
        self._profile_status.configure(text=msg, text_color=color)
        # Auto-czyszczenie po 4 sekundach
        self.after(4000, lambda: self._profile_status.configure(text=""))

    # ── Zakładka: Zarządzanie HRID ─────────────────────────────────────────
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

    # ── Zakładka: Diagnostyka ──────────────────────────────────────────────
    def _build_diagnostics_tab(self):
        tab = self.tabs.tab("Diagnostyka")
        ctk.CTkLabel(
            tab,
            text="Tu będzie weryfikacja połączenia RS-232.",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["muted"]
        ).pack(pady=40)