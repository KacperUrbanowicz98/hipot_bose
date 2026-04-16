import threading
import serial.tools.list_ports
import customtkinter as ctk
from config import COLORS, load_config, save_config
from hipot_controller import HipotController


class EngineerPanel(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Panel Inżynieryjny")
        self.geometry("820x680")
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
        self.tabs.add("SN Prefix Map")
        self.tabs.add("Zarządzanie HRID")
        self.tabs.add("Konfiguracja portu")
        self.tabs.add("Diagnostyka")

        self._build_profiles_tab()
        self._build_sn_map_tab()
        self._build_hrid_tab()
        self._build_port_tab()
        self._build_diagnostics_tab()

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Profile testowe
    # ══════════════════════════════════════════════════════════════════════
    def _build_profiles_tab(self):
        tab = self.tabs.tab("Profile testowe")
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab, text="Profile testowe",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(8, 12))

        # Lewa kolumna — lista profili
        left = ctk.CTkFrame(tab, fg_color=COLORS["surface"], corner_radius=8, width=180)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        left.grid_propagate(False)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self._profile_listbox = ctk.CTkScrollableFrame(
            left, fg_color="transparent"
        )
        self._profile_listbox.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        btn_list_frame = ctk.CTkFrame(left, fg_color="transparent")
        btn_list_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))

        ctk.CTkButton(
            btn_list_frame, text="➕ Nowy",
            font=ctk.CTkFont(size=12), height=30,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            corner_radius=6,
            command=self._new_profile
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))

        ctk.CTkButton(
            btn_list_frame, text="🗑",
            font=ctk.CTkFont(size=12), height=30, width=36,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["fail"],
            corner_radius=6,
            command=self._delete_profile
        ).pack(side="left")

        # Prawa kolumna — formularz edycji
        right = ctk.CTkFrame(tab, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_columnconfigure(1, weight=1)

        fields = [
            ("Klucz profilu", "key", False),
            ("Nazwa profilu", "name", False),
            ("Typ testu", "type", False),
            ("Napięcie (kV)", "voltage", False),
            ("HI limit (mA)", "hi_limit", False),
            ("LO limit (mA)", "lo_limit", False),
            ("Ramp-up (s)", "ramp", False),
            ("Dwell (s)", "dwell", False),
            ("Częstotliwość", "frequency", True),
        ]

        self._prof_entries = {}
        self._freq_var = ctk.StringVar(value="50 Hz (0)")
        self._selected_profile_key = None

        for i, (label, key, is_freq) in enumerate(fields):
            ctk.CTkLabel(
                right, text=label,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["muted"],
                anchor="w", width=140
            ).grid(row=i, column=0, sticky="w", padx=(0, 10), pady=3)

            if is_freq:
                widget = ctk.CTkOptionMenu(
                    right,
                    values=["50 Hz (0)", "60 Hz (1)"],
                    variable=self._freq_var,
                    fg_color=COLORS["card"],
                    button_color=COLORS["primary"],
                    button_hover_color="#005a9e",
                    font=ctk.CTkFont(size=13),
                    width=240
                )
                widget.grid(row=i, column=1, sticky="w", pady=3)
                self._prof_entries[key] = widget
            else:
                entry = ctk.CTkEntry(
                    right, font=ctk.CTkFont(size=13),
                    height=34, width=240, corner_radius=8,
                    border_color=COLORS["border"]
                )
                entry.grid(row=i, column=1, sticky="w", pady=3)
                self._prof_entries[key] = entry

            # Klucz profilu — zablokowany przy edycji, odblokowany przy nowym
            if key == "key":
                self._prof_entries["key"].configure(
                    placeholder_text="np. 3KV lub 2.5KV"
                )

        self._profile_status = ctk.CTkLabel(
            right, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"]
        )
        self._profile_status.grid(
            row=len(fields), column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        btn_frame = ctk.CTkFrame(right, fg_color="transparent")
        btn_frame.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ctk.CTkButton(
            btn_frame, text="💾  Zapisz",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36, width=130, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._save_profile
        ).grid(row=0, column=0, padx=(0, 10))

        ctk.CTkButton(
            btn_frame, text="✕  Anuluj",
            font=ctk.CTkFont(size=13), height=36, width=100,
            corner_radius=8, fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._cancel_profile_edit
        ).grid(row=0, column=1)

        tab.grid_columnconfigure(0, weight=0)
        tab.grid_columnconfigure(1, weight=1)

        self._refresh_profile_list()

    def _refresh_profile_list(self):
        for widget in self._profile_listbox.winfo_children():
            widget.destroy()

        config = load_config()
        profiles = config.get("profiles", {})

        for key in sorted(profiles.keys()):
            btn = ctk.CTkButton(
                self._profile_listbox,
                text=key,
                font=ctk.CTkFont(size=13),
                height=34, corner_radius=6,
                fg_color=COLORS["primary"] if key == self._selected_profile_key else "transparent",
                hover_color="#005a9e",
                border_width=1,
                border_color=COLORS["border"] if key != self._selected_profile_key else COLORS["primary"],
                text_color=COLORS["text"],
                anchor="w",
                command=lambda k=key: self._select_profile(k)
            )
            btn.pack(fill="x", pady=2)

        # Auto-select tylko przy pierwszym otwarciu, NIE przy _new_profile
        if profiles and self._selected_profile_key is None and not hasattr(self, '_new_profile_mode'):
            first = sorted(profiles.keys())[0]
            self._select_profile(first)

    def _select_profile(self, key):
        self._new_profile_mode = False
        self._selected_profile_key = key
        config = load_config()
        profile = config.get("profiles", {}).get(key, {})

        self._prof_entries["key"].configure(state="normal")
        self._prof_entries["key"].delete(0, "end")
        self._prof_entries["key"].insert(0, key)
        self._prof_entries["key"].configure(state="disabled")

        for field_key in ["name", "type", "voltage", "hi_limit", "lo_limit", "ramp", "dwell"]:
            entry = self._prof_entries[field_key]
            entry.delete(0, "end")
            entry.insert(0, str(profile.get(field_key, "")))

        freq = str(profile.get("frequency", 0))
        self._freq_var.set("50 Hz (0)" if freq == "0" else "60 Hz (1)")

        self._refresh_profile_list()

    def _new_profile(self):
        self._selected_profile_key = None
        self._new_profile_mode = True

        # Odblokuj pole klucza
        key_entry = self._prof_entries["key"]
        key_entry.configure(state="normal")
        key_entry.delete(0, "end")

        # Wyczyść pozostałe pola
        for field_key in ["name", "type", "voltage", "hi_limit", "lo_limit", "ramp", "dwell"]:
            self._prof_entries[field_key].delete(0, "end")

        self._freq_var.set("50 Hz (0)")

        # Odśwież listę BEZ auto-select pierwszego elementu
        for widget in self._profile_listbox.winfo_children():
            widget.destroy()
        config = load_config()
        for key in sorted(config.get("profiles", {}).keys()):
            btn = ctk.CTkButton(
                self._profile_listbox,
                text=key,
                font=ctk.CTkFont(size=13),
                height=34, corner_radius=6,
                fg_color="transparent",
                hover_color="#005a9e",
                border_width=1,
                border_color=COLORS["border"],
                text_color=COLORS["text"],
                anchor="w",
                command=lambda k=key: self._select_profile(k)
            )
            btn.pack(fill="x", pady=2)

        key_entry.focus()
        self._show_profile_status("Wprowadź dane nowego profilu i kliknij Zapisz", COLORS["muted"])

    def _delete_profile(self):
        if not self._selected_profile_key:
            self._show_profile_status("⚠  Zaznacz profil do usunięcia", COLORS["warning"])
            return

        config = load_config()
        config.get("profiles", {}).pop(self._selected_profile_key, None)
        save_config(config)

        deleted = self._selected_profile_key
        self._selected_profile_key = None

        for field_key in ["name", "type", "voltage", "hi_limit", "lo_limit", "ramp", "dwell"]:
            self._prof_entries[field_key].delete(0, "end")
        self._prof_entries["key"].configure(state="normal")
        self._prof_entries["key"].delete(0, "end")

        self._refresh_profile_list()
        self._show_profile_status(f"🗑  Usunięto profil '{deleted}'", COLORS["muted"])

    def _cancel_profile_edit(self):
        if self._selected_profile_key:
            self._select_profile(self._selected_profile_key)
        else:
            for field_key in ["name", "type", "voltage", "hi_limit", "lo_limit", "ramp", "dwell"]:
                self._prof_entries[field_key].delete(0, "end")
            self._prof_entries["key"].configure(state="normal")
            self._prof_entries["key"].delete(0, "end")

    def _save_profile(self):
        try:
            key_entry = self._prof_entries["key"]
            key_entry.configure(state="normal")
            new_key = key_entry.get().strip()

            if not new_key:
                self._show_profile_status("⚠  Klucz profilu nie może być pusty", COLORS["fail"])
                return

            name = self._prof_entries["name"].get().strip()
            ptype = self._prof_entries["type"].get().strip().upper()
            voltage = float(self._prof_entries["voltage"].get().strip())
            hi_limit = float(self._prof_entries["hi_limit"].get().strip())
            lo_limit = float(self._prof_entries["lo_limit"].get().strip())
            ramp = float(self._prof_entries["ramp"].get().strip())
            dwell = float(self._prof_entries["dwell"].get().strip())
            frequency = 0 if "50" in self._freq_var.get() else 1

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

            config = load_config()

            # Jeśli zmieniono klucz — usuń stary
            if self._selected_profile_key and self._selected_profile_key != new_key:
                config.get("profiles", {}).pop(self._selected_profile_key, None)
                # Zaktualizuj też sn_prefix_map
                for prefix, pk in config.get("sn_prefix_map", {}).items():
                    if pk == self._selected_profile_key:
                        config["sn_prefix_map"][prefix] = new_key

            config.setdefault("profiles", {})[new_key] = {
                "name": name, "type": ptype,
                "voltage": voltage, "hi_limit": hi_limit,
                "lo_limit": lo_limit, "ramp": ramp,
                "dwell": dwell, "frequency": frequency
            }
            save_config(config)

            self._selected_profile_key = new_key
            key_entry.configure(state="disabled")
            self._refresh_profile_list()
            self._show_profile_status(f"✔  Profil '{new_key}' zapisany", COLORS["success"])

        except ValueError:
            self._show_profile_status("⚠  Nieprawidłowa wartość — sprawdź pola", COLORS["fail"])

    def _show_profile_status(self, msg, color):
        self._profile_status.configure(text=msg, text_color=color)
        self.after(4000, lambda: self._profile_status.configure(text=""))
    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: SN Prefix Map
    # ══════════════════════════════════════════════════════════════════════
    def _build_sn_map_tab(self):
        tab = self.tabs.tab("SN Prefix Map")
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab, text="Mapowanie prefiksów SN na profile",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(8, 12))

        # Scrollable lista prefiksów
        self._sn_scroll = ctk.CTkScrollableFrame(
            tab, fg_color=COLORS["surface"], corner_radius=8
        )
        self._sn_scroll.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(0, 12))
        self._sn_scroll.grid_columnconfigure(0, weight=1)
        self._sn_scroll.grid_columnconfigure(1, weight=1)

        self._sn_rows = []
        self._refresh_sn_list()

        # Dodawanie nowego prefixu
        add_frame = ctk.CTkFrame(tab, fg_color="transparent")
        add_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
        add_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            add_frame, text="Nowy prefiks (6 cyfr):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, padx=(0, 8))

        self._new_prefix_entry = ctk.CTkEntry(
            add_frame, font=ctk.CTkFont(size=13),
            height=34, width=120, corner_radius=8,
            border_color=COLORS["border"],
            placeholder_text="np. 084999"
        )
        self._new_prefix_entry.grid(row=0, column=1, sticky="w", padx=(0, 8))

        config = load_config()
        profile_keys = list(config.get("profiles", {}).keys())
        self._new_prefix_profile_var = ctk.StringVar(
            value=profile_keys[0] if profile_keys else "3KV"
        )

        ctk.CTkOptionMenu(
            add_frame,
            values=profile_keys,
            variable=self._new_prefix_profile_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=140
        ).grid(row=0, column=2, padx=(0, 8))

        ctk.CTkButton(
            add_frame, text="➕  Dodaj",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=34, width=100, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._add_sn_prefix
        ).grid(row=0, column=3)

        self._sn_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"]
        )
        self._sn_status.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _refresh_sn_list(self):
        for widget in self._sn_scroll.winfo_children():
            widget.destroy()
        self._sn_rows = []

        config = load_config()
        sn_map = config.get("sn_prefix_map", {})

        # Nagłówki
        ctk.CTkLabel(
            self._sn_scroll, text="Prefiks SN",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(4, 2))
        ctk.CTkLabel(
            self._sn_scroll, text="Profil",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=1, sticky="w", padx=8, pady=(4, 2))

        for i, (prefix, profile_key) in enumerate(sorted(sn_map.items())):
            ctk.CTkLabel(
                self._sn_scroll, text=prefix,
                font=ctk.CTkFont(size=13),
                text_color=COLORS["text"]
            ).grid(row=i + 1, column=0, sticky="w", padx=8, pady=2)

            ctk.CTkLabel(
                self._sn_scroll, text=profile_key,
                font=ctk.CTkFont(size=13),
                text_color=COLORS["primary"]
            ).grid(row=i + 1, column=1, sticky="w", padx=8, pady=2)

            ctk.CTkButton(
                self._sn_scroll, text="🗑",
                width=32, height=26,
                font=ctk.CTkFont(size=12),
                fg_color="transparent",
                hover_color=COLORS["fail"],
                border_width=1,
                border_color=COLORS["border"],
                command=lambda p=prefix: self._delete_sn_prefix(p)
            ).grid(row=i + 1, column=2, padx=(8, 4), pady=2)

    def _add_sn_prefix(self):
        prefix = self._new_prefix_entry.get().strip()
        profile_key = self._new_prefix_profile_var.get()

        if len(prefix) != 6 or not prefix.isdigit():
            self._show_sn_status("⚠  Prefiks musi mieć dokładnie 6 cyfr", COLORS["fail"])
            return

        config = load_config()
        if prefix in config.get("sn_prefix_map", {}):
            self._show_sn_status(f"⚠  Prefiks {prefix} już istnieje", COLORS["warning"])
            return

        config.setdefault("sn_prefix_map", {})[prefix] = profile_key
        save_config(config)
        self._new_prefix_entry.delete(0, "end")
        self._refresh_sn_list()
        self._show_sn_status(f"✔  Dodano prefiks {prefix} → {profile_key}", COLORS["success"])

    def _delete_sn_prefix(self, prefix):
        config = load_config()
        config.get("sn_prefix_map", {}).pop(prefix, None)
        save_config(config)
        self._refresh_sn_list()
        self._show_sn_status(f"🗑  Usunięto prefiks {prefix}", COLORS["muted"])

    def _show_sn_status(self, msg, color):
        self._sn_status.configure(text=msg, text_color=color)
        self.after(4000, lambda: self._sn_status.configure(text=""))

    def _refresh_sn_list(self):
        for widget in self._sn_scroll.winfo_children():
            widget.destroy()
        self._sn_rows = []

        config = load_config()
        sn_map = config.get("sn_prefix_map", {})

        # Nagłówki
        ctk.CTkLabel(
            self._sn_scroll, text="Prefiks SN",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(4, 2))
        ctk.CTkLabel(
            self._sn_scroll, text="Profil",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=1, sticky="w", padx=8, pady=(4, 2))

        for i, (prefix, profile_key) in enumerate(sorted(sn_map.items())):
            ctk.CTkLabel(
                self._sn_scroll, text=prefix,
                font=ctk.CTkFont(size=13),
                text_color=COLORS["text"]
            ).grid(row=i + 1, column=0, sticky="w", padx=8, pady=2)

            ctk.CTkLabel(
                self._sn_scroll, text=profile_key,
                font=ctk.CTkFont(size=13),
                text_color=COLORS["primary"]
            ).grid(row=i + 1, column=1, sticky="w", padx=8, pady=2)

            ctk.CTkButton(
                self._sn_scroll, text="🗑",
                width=32, height=26,
                font=ctk.CTkFont(size=12),
                fg_color="transparent",
                hover_color=COLORS["fail"],
                border_width=1,
                border_color=COLORS["border"],
                command=lambda p=prefix: self._delete_sn_prefix(p)
            ).grid(row=i + 1, column=2, padx=(8, 4), pady=2)

    def _add_sn_prefix(self):
        prefix = self._new_prefix_entry.get().strip()
        profile_key = self._new_prefix_profile_var.get()

        if len(prefix) != 6 or not prefix.isdigit():
            self._show_sn_status("⚠  Prefiks musi mieć dokładnie 6 cyfr", COLORS["fail"])
            return

        config = load_config()
        if prefix in config.get("sn_prefix_map", {}):
            self._show_sn_status(f"⚠  Prefiks {prefix} już istnieje", COLORS["warning"])
            return

        config.setdefault("sn_prefix_map", {})[prefix] = profile_key
        save_config(config)
        self._new_prefix_entry.delete(0, "end")
        self._refresh_sn_list()
        self._show_sn_status(f"✔  Dodano prefiks {prefix} → {profile_key}", COLORS["success"])

    def _delete_sn_prefix(self, prefix):
        config = load_config()
        config.get("sn_prefix_map", {}).pop(prefix, None)
        save_config(config)
        self._refresh_sn_list()
        self._show_sn_status(f"🗑  Usunięto prefiks {prefix}", COLORS["muted"])

    def _show_sn_status(self, msg, color):
        self._sn_status.configure(text=msg, text_color=color)
        self.after(4000, lambda: self._sn_status.configure(text=""))

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Zarządzanie HRID
    # ══════════════════════════════════════════════════════════════════════
    def _build_hrid_tab(self):
        tab = self.tabs.tab("Zarządzanie HRID")
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab, text="Użytkownicy systemu",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(8, 12))

        self._hrid_scroll = ctk.CTkScrollableFrame(
            tab, fg_color=COLORS["surface"], corner_radius=8
        )
        self._hrid_scroll.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(0, 12))
        self._hrid_scroll.grid_columnconfigure(0, weight=1)
        self._hrid_scroll.grid_columnconfigure(1, weight=1)

        self._refresh_hrid_list()

        # Dodawanie użytkownika
        add_frame = ctk.CTkFrame(tab, fg_color="transparent")
        add_frame.grid(row=2, column=0, columnspan=4, sticky="ew")

        ctk.CTkLabel(
            add_frame, text="HRID:",
            font=ctk.CTkFont(size=12), text_color=COLORS["muted"]
        ).grid(row=0, column=0, padx=(0, 6))

        self._new_hrid_entry = ctk.CTkEntry(
            add_frame, font=ctk.CTkFont(size=13),
            height=34, width=120, corner_radius=8,
            border_color=COLORS["border"],
            placeholder_text="np. 12345678"
        )
        self._new_hrid_entry.grid(row=0, column=1, padx=(0, 8))

        ctk.CTkLabel(
            add_frame, text="Imię i nazwisko:",
            font=ctk.CTkFont(size=12), text_color=COLORS["muted"]
        ).grid(row=0, column=2, padx=(0, 6))

        self._new_hrid_name_entry = ctk.CTkEntry(
            add_frame, font=ctk.CTkFont(size=13),
            height=34, width=160, corner_radius=8,
            border_color=COLORS["border"],
            placeholder_text="Jan Kowalski"
        )
        self._new_hrid_name_entry.grid(row=0, column=3, padx=(0, 8))

        self._new_hrid_role_var = ctk.StringVar(value="operator")
        ctk.CTkOptionMenu(
            add_frame,
            values=["operator", "engineer"],
            variable=self._new_hrid_role_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=120
        ).grid(row=0, column=4, padx=(0, 8))

        ctk.CTkButton(
            add_frame, text="➕  Dodaj",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=34, width=100, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._add_hrid_user
        ).grid(row=0, column=5)

        self._hrid_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"]
        )
        self._hrid_status.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _refresh_hrid_list(self):
        for widget in self._hrid_scroll.winfo_children():
            widget.destroy()

        config = load_config()
        users = config.get("users", {})

        ctk.CTkLabel(
            self._hrid_scroll, text="HRID",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(4, 2))
        ctk.CTkLabel(
            self._hrid_scroll, text="Imię i nazwisko",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=1, sticky="w", padx=8, pady=(4, 2))
        ctk.CTkLabel(
            self._hrid_scroll, text="Rola",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=2, sticky="w", padx=8, pady=(4, 2))

        for i, (hrid, data) in enumerate(sorted(users.items())):
            ctk.CTkLabel(
                self._hrid_scroll, text=hrid,
                font=ctk.CTkFont(size=13), text_color=COLORS["text"]
            ).grid(row=i + 1, column=0, sticky="w", padx=8, pady=2)

            ctk.CTkLabel(
                self._hrid_scroll, text=data.get("name", ""),
                font=ctk.CTkFont(size=13), text_color=COLORS["text"]
            ).grid(row=i + 1, column=1, sticky="w", padx=8, pady=2)

            role = data.get("role", "operator")
            ctk.CTkLabel(
                self._hrid_scroll, text=role,
                font=ctk.CTkFont(size=13),
                text_color=COLORS["primary"] if role == "engineer" else COLORS["muted"]
            ).grid(row=i + 1, column=2, sticky="w", padx=8, pady=2)

            ctk.CTkButton(
                self._hrid_scroll, text="🗑",
                width=32, height=26,
                font=ctk.CTkFont(size=12),
                fg_color="transparent",
                hover_color=COLORS["fail"],
                border_width=1, border_color=COLORS["border"],
                command=lambda h=hrid: self._delete_hrid_user(h)
            ).grid(row=i + 1, column=3, padx=(8, 4), pady=2)

    def _add_hrid_user(self):
        hrid = self._new_hrid_entry.get().strip()
        name = self._new_hrid_name_entry.get().strip()
        role = self._new_hrid_role_var.get()

        if not hrid or not name:
            self._show_hrid_status("⚠  Wypełnij HRID i imię", COLORS["fail"])
            return

        config = load_config()
        if hrid in config.get("users", {}):
            self._show_hrid_status(f"⚠  HRID {hrid} już istnieje", COLORS["warning"])
            return

        config.setdefault("users", {})[hrid] = {"name": name, "role": role}
        save_config(config)
        self._new_hrid_entry.delete(0, "end")
        self._new_hrid_name_entry.delete(0, "end")
        self._refresh_hrid_list()
        self._show_hrid_status(f"✔  Dodano użytkownika {name} ({hrid})", COLORS["success"])

    def _delete_hrid_user(self, hrid):
        config = load_config()
        config.get("users", {}).pop(hrid, None)
        save_config(config)
        self._refresh_hrid_list()
        self._show_hrid_status(f"🗑  Usunięto użytkownika {hrid}", COLORS["muted"])

    def _show_hrid_status(self, msg, color):
        self._hrid_status.configure(text=msg, text_color=color)
        self.after(4000, lambda: self._hrid_status.configure(text=""))

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Konfiguracja portu
    # ══════════════════════════════════════════════════════════════════════
    def _build_port_tab(self):
        tab = self.tabs.tab("Konfiguracja portu")
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            tab, text="Konfiguracja RS-232",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(8, 16))

        config = load_config()
        serial_cfg = config.get("serial", {})

        # Port COM
        ctk.CTkLabel(
            tab, text="Port COM:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=160
        ).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)

        port_frame = ctk.CTkFrame(tab, fg_color="transparent")
        port_frame.grid(row=1, column=1, sticky="w", pady=6)

        self._port_var = ctk.StringVar(value=serial_cfg.get("port", "COM11"))
        available_ports = [p.device for p in serial.tools.list_ports.comports()]
        if not available_ports:
            available_ports = [serial_cfg.get("port", "COM11")]

        self._port_menu = ctk.CTkOptionMenu(
            port_frame,
            values=available_ports,
            variable=self._port_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=140
        )
        self._port_menu.grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            port_frame, text="↺  Odśwież",
            font=ctk.CTkFont(size=12),
            height=32, width=100, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._refresh_ports
        ).grid(row=0, column=1)

        # Baudrate
        ctk.CTkLabel(
            tab, text="Baudrate:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=160
        ).grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)

        self._baud_var = ctk.StringVar(value=str(serial_cfg.get("baudrate", 9600)))
        ctk.CTkOptionMenu(
            tab,
            values=["9600", "19200", "38400", "57600", "115200"],
            variable=self._baud_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=140
        ).grid(row=2, column=1, sticky="w", pady=6)

        # Timeout
        ctk.CTkLabel(
            tab, text="Timeout (s):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=160
        ).grid(row=3, column=0, sticky="w", padx=(0, 12), pady=6)

        self._timeout_entry = ctk.CTkEntry(
            tab, font=ctk.CTkFont(size=13),
            height=36, width=140, corner_radius=8,
            border_color=COLORS["border"]
        )
        self._timeout_entry.insert(0, str(serial_cfg.get("timeout", 3)))
        self._timeout_entry.grid(row=3, column=1, sticky="w", pady=6)

        self._port_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"]
        )
        self._port_status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

        ctk.CTkButton(
            tab, text="💾  Zapisz konfigurację",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38, width=200, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._save_port_config
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports:
            ports = ["Brak portów"]
        self._port_menu.configure(values=ports)
        if ports:
            self._port_var.set(ports[0])

    def _save_port_config(self):
        try:
            timeout = int(self._timeout_entry.get().strip())
            config = load_config()
            config["serial"] = {
                "port": self._port_var.get(),
                "baudrate": int(self._baud_var.get()),
                "timeout": timeout
            }
            save_config(config)
            self._port_status.configure(
                text="✔  Konfiguracja zapisana", text_color=COLORS["success"]
            )
            self.after(4000, lambda: self._port_status.configure(text=""))
        except ValueError:
            self._port_status.configure(
                text="⚠  Nieprawidłowy timeout", text_color=COLORS["fail"]
            )

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Diagnostyka
    # ══════════════════════════════════════════════════════════════════════
    def _build_diagnostics_tab(self):
        tab = self.tabs.tab("Diagnostyka")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            tab, text="Diagnostyka połączenia RS-232",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, sticky="w", pady=(8, 16))

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(row=1, column=0, sticky="w", pady=(0, 12))

        ctk.CTkButton(
            btn_frame, text="🔌  Test połączenia (RESET)",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38, width=220, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._run_connection_test
        ).grid(row=0, column=0, padx=(0, 12))

        ctk.CTkButton(
            btn_frame, text="📋  Wyślij komendę ręcznie",
            font=ctk.CTkFont(size=13),
            height=38, width=200, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._send_manual_command
        ).grid(row=0, column=1, padx=(0, 12))

        ctk.CTkButton(
            btn_frame, text="🗑  Wyczyść log",
            font=ctk.CTkFont(size=13),
            height=38, width=130, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._clear_log
        ).grid(row=0, column=2)

        # Pole ręcznej komendy
        manual_frame = ctk.CTkFrame(tab, fg_color="transparent")
        manual_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        manual_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            manual_frame, text="Komenda:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        cmd_row = ctk.CTkFrame(manual_frame, fg_color="transparent")
        cmd_row.grid(row=1, column=0, sticky="ew")
        cmd_row.grid_columnconfigure(0, weight=1)

        self._manual_cmd_entry = ctk.CTkEntry(
            cmd_row, font=ctk.CTkFont(size=13),
            height=36, corner_radius=8,
            border_color=COLORS["border"],
            placeholder_text="np. *IDN?  lub  SA 3000"
        )
        self._manual_cmd_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._manual_cmd_entry.bind("<Return>", lambda e: self._send_manual_command())

        ctk.CTkButton(
            cmd_row, text="Wyślij",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36, width=80, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._send_manual_command
        ).grid(row=0, column=1)

        # Log output
        ctk.CTkLabel(
            tab, text="Log komunikacji:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"]
        ).grid(row=3, column=0, sticky="w", pady=(8, 4))

        self._diag_log = ctk.CTkTextbox(
            tab,
            font=ctk.CTkFont(family="Courier", size=12),
            fg_color=COLORS["surface"],
            text_color=COLORS["text"],
            corner_radius=8,
            height=260
        )
        self._diag_log.grid(row=4, column=0, sticky="nsew", pady=(0, 0))
        self._diag_log.configure(state="disabled")

    def _log(self, msg: str, color_tag: str = "normal"):
        self._diag_log.configure(state="normal")
        self._diag_log.insert("end", msg + "\n")
        self._diag_log.see("end")
        self._diag_log.configure(state="disabled")

    def _clear_log(self):
        self._diag_log.configure(state="normal")
        self._diag_log.delete("1.0", "end")
        self._diag_log.configure(state="disabled")

    def _run_connection_test(self):
        self._log("── Test połączenia ──────────────────────")
        threading.Thread(target=self._do_connection_test, daemon=True).start()

    def _do_connection_test(self):
        config = load_config()
        serial_cfg = config.get("serial", {})
        port = serial_cfg.get("port", "COM11")
        baud = serial_cfg.get("baudrate", 9600)
        self.after(0, self._log, f"Port: {port} | Baudrate: {baud}")

        ctrl = HipotController(port=port, baudrate=baud, timeout=3)
        try:
            ctrl.connect()
            self.after(0, self._log, "✔  Połączono z portem")

            resp = ctrl._send("RESET", wait=0.4)
            ack = "✔  ACK" if b'\x06' in resp else "✘  NAK / brak odpowiedzi"
            self.after(0, self._log, f"SEND >> RESET | {ack} | raw: {resp!r}")

        except Exception as e:
            self.after(0, self._log, f"❌  Błąd połączenia: {e}")
        finally:
            ctrl.disconnect()
            self.after(0, self._log, "── Koniec testu ─────────────────────────")

    def _send_manual_command(self):
        cmd = self._manual_cmd_entry.get().strip()
        if not cmd:
            return
        threading.Thread(target=self._do_manual_command, args=(cmd,), daemon=True).start()

    def _do_manual_command(self, cmd):
        config = load_config()
        serial_cfg = config.get("serial", {})
        ctrl = HipotController(
            port=serial_cfg.get("port", "COM11"),
            baudrate=serial_cfg.get("baudrate", 9600),
            timeout=3
        )
        try:
            ctrl.connect()
            if cmd.endswith("?"):
                resp = ctrl._query(cmd, wait=0.5)
                self.after(0, self._log, f"QUERY >> {cmd!r:20} | RESP << {resp!r}")
            else:
                resp = ctrl._send(cmd, wait=0.5)
                ack = "ACK ✔" if b'\x06' in resp else "NAK ✘"
                self.after(0, self._log, f"SEND  >> {cmd!r:20} | {ack} | raw: {resp!r}")
        except Exception as e:
            self.after(0, self._log, f"❌  {e}")
        finally:
            ctrl.disconnect()