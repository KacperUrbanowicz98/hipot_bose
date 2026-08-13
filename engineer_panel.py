"""
engineer_panel.py
-----------------
Panel Inżynieryjny.

Zmiany:

  1. Blokada akcji sprzętowych w trakcie testu. Przyciski "→ PE", "→ HIPOT",
     "Test połączenia (RESET)" i ręczne komendy otwierały własne połączenia
     bez sprawdzenia, czy w tle nie trwa sekwencja. grab_set() blokuje tylko
     interakcję z oknem, nie wątek roboczy — groziło to przełączeniem styków
     pod napięciem albo RESET-em w połowie testu.

  2. Log audytowy każdej zmiany: kto, kiedy, co z czego na co. Wcześniej
     zmiana napięcia testu albo limitów nie zostawiała żadnego śladu.

  3. Pełniejsza walidacja parametrów (zakresy z config.LIMITS), w tym pól
     Ground Bond, które wcześniej przyjmowały wartości ujemne.

  4. Odświeżenie listy portów nie kasuje już świadomego wyboru inżyniera.

  5. Nowa zakładka Bezpieczeństwo: zmiana hasła inżynieryjnego i podgląd
     logu audytowego.

  6. Nowa sekcja Parametry testu: margines czasu, opóźnienie przekaźnika,
     wymóg przekaźnika przy Ground Bond, kolejność pól wyniku GND.
"""

from __future__ import annotations

import threading

import customtkinter as ctk
import serial.tools.list_ports

import runtime_state
from app_logging import audit, get_logger, read_audit_tail
from config import (
    COLORS,
    check_range,
    load_config,
    save_config,
    set_password,
    verify_password,
)
from hipot_controller import HipotController

log = get_logger(__name__)


class EngineerPanel(ctk.CTkToplevel):
    def __init__(self, parent, actor: str = ""):
        super().__init__(parent)
        self.actor = actor or "UNKNOWN"

        self.title("Panel Inżynieryjny")
        self.geometry("900x760")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self.grab_set()

        log.info("Otwarto Panel Inżynieryjny (%s)", self.actor)
        self._build()

    # ══════════════════════════════════════════════════════════════════════
    # Wspólne
    # ══════════════════════════════════════════════════════════════════════
    def _blocked_by_test(self, status_setter=None) -> bool:
        """
        True, jeżeli trwa test i akcja sprzętowa musi zostać zablokowana.
        """
        if not runtime_state.test_in_progress():
            return False

        message = runtime_state.guard_message()
        log.warning("Akcja panelu zablokowana — trwa test.")

        if status_setter:
            status_setter(message.splitlines()[0], COLORS["fail"])

        try:
            from tkinter import messagebox
            messagebox.showwarning("Test w toku", message, parent=self)
        except Exception:
            pass

        return True

    def _build(self):
        ctk.CTkLabel(
            self,
            text="🔧 Panel Inżynieryjny",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text"],
        ).pack(pady=(20, 4), padx=24, anchor="w")

        ctk.CTkLabel(
            self,
            text=f"Zalogowany: {self.actor}   •   każda zmiana trafia do "
                 f"logs/config_audit.log",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        ).pack(pady=(0, 10), padx=24, anchor="w")

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

        for name in [
            "Profile testowe",
            "SN Prefix Map",
            "Zarządzanie HRID",
            "Konfiguracja portu",
            "Relay (ESP)",
            "Diagnostyka",
            "Bezpieczeństwo",
        ]:
            self.tabs.add(name)

        self._build_profiles_tab()
        self._build_sn_map_tab()
        self._build_hrid_tab()
        self._build_port_tab()
        self._build_relay_tab()
        self._build_diagnostics_tab()
        self._build_security_tab()

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Profile testowe
    # ══════════════════════════════════════════════════════════════════════
    def _build_profiles_tab(self):
        tab = self.tabs.tab("Profile testowe")
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=0)
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            tab, text="Profile testowe",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(8, 12))

        left = ctk.CTkFrame(tab, fg_color=COLORS["surface"], corner_radius=8, width=180)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        left.grid_propagate(False)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self._profile_listbox = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._profile_listbox.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        btn_list_frame = ctk.CTkFrame(left, fg_color="transparent")
        btn_list_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))

        ctk.CTkButton(
            btn_list_frame, text="➕ Nowy",
            font=ctk.CTkFont(size=12), height=30,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            corner_radius=6, command=self._new_profile,
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))

        ctk.CTkButton(
            btn_list_frame, text="🗑",
            font=ctk.CTkFont(size=12), height=30, width=36,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["fail"],
            corner_radius=6, command=self._delete_profile,
        ).pack(side="left")

        right = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_columnconfigure(1, weight=1)

        hipot_fields = [
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
        self._new_profile_mode = False

        for i, (label, key, is_freq) in enumerate(hipot_fields):
            ctk.CTkLabel(
                right, text=label,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["muted"],
                anchor="w", width=160,
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
                    width=240,
                )
                widget.grid(row=i, column=1, sticky="w", pady=3)
                self._prof_entries[key] = widget
            else:
                entry = ctk.CTkEntry(
                    right, font=ctk.CTkFont(size=13),
                    height=34, width=240, corner_radius=8,
                    border_color=COLORS["border"],
                )
                entry.grid(row=i, column=1, sticky="w", pady=3)
                self._prof_entries[key] = entry

        n = len(hipot_fields)

        ctk.CTkFrame(right, fg_color=COLORS["border"], height=1).grid(
            row=n, column=0, columnspan=2, sticky="ew", pady=(12, 8)
        )

        self._gnd_enabled_var = ctk.BooleanVar(value=False)
        gnd_toggle_frame = ctk.CTkFrame(right, fg_color="transparent")
        gnd_toggle_frame.grid(row=n + 1, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ctk.CTkLabel(
            gnd_toggle_frame, text="Ground Bond",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text"],
        ).pack(side="left", padx=(0, 12))

        self._gnd_toggle = ctk.CTkSwitch(
            gnd_toggle_frame,
            text="wyłączony",
            variable=self._gnd_enabled_var,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            progress_color=COLORS["primary"],
            command=self._on_gnd_toggle,
        )
        self._gnd_toggle.pack(side="left")

        self._gnd_frame = ctk.CTkFrame(right, fg_color="transparent")
        self._gnd_frame.grid(row=n + 2, column=0, columnspan=2, sticky="ew")
        self._gnd_frame.grid_columnconfigure(1, weight=1)
        self._gnd_frame.grid_remove()

        gnd_fields = [
            ("Prąd GND (A)", "gnd_current"),
            ("HI limit GND (mΩ)", "gnd_hi_limit"),
            ("LO limit GND (mΩ)", "gnd_lo_limit"),
            ("Dwell GND (s)", "gnd_dwell"),
            ("Offset GND (mΩ)", "gnd_offset"),
        ]

        self._gnd_entries = {}
        self._gnd_freq_var = ctk.StringVar(value="60 Hz (1)")

        for i, (label, key) in enumerate(gnd_fields):
            ctk.CTkLabel(
                self._gnd_frame, text=label,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["muted"],
                anchor="w", width=160,
            ).grid(row=i, column=0, sticky="w", padx=(0, 10), pady=3)

            entry = ctk.CTkEntry(
                self._gnd_frame, font=ctk.CTkFont(size=13),
                height=34, width=180, corner_radius=8,
                border_color=COLORS["border"],
            )
            entry.grid(row=i, column=1, sticky="w", pady=3)
            self._gnd_entries[key] = entry

        ctk.CTkLabel(
            self._gnd_frame, text="Częstotliwość GND",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            anchor="w", width=160,
        ).grid(row=len(gnd_fields), column=0, sticky="w", padx=(0, 10), pady=3)

        ctk.CTkOptionMenu(
            self._gnd_frame,
            values=["60 Hz (1)", "50 Hz (0)"],
            variable=self._gnd_freq_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=180,
        ).grid(row=len(gnd_fields), column=1, sticky="w", pady=3)

        ctk.CTkLabel(
            self._gnd_frame,
            text="⚠ Profil z Ground Bond wymaga skonfigurowanego portu ESP "
                 "(zakładka Relay).",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["warning"],
            wraplength=420, justify="left",
        ).grid(row=len(gnd_fields) + 1, column=0, columnspan=2,
               sticky="w", pady=(8, 0))

        self._profile_status = ctk.CTkLabel(
            right, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"],
            wraplength=420, justify="left",
        )
        self._profile_status.grid(row=n + 3, column=0, columnspan=2,
                                  sticky="w", pady=(8, 0))

        btn_frame = ctk.CTkFrame(right, fg_color="transparent")
        btn_frame.grid(row=n + 4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ctk.CTkButton(
            btn_frame, text="💾 Zapisz",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36, width=130, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._save_profile,
        ).grid(row=0, column=0, padx=(0, 10))

        ctk.CTkButton(
            btn_frame, text="✕ Anuluj",
            font=ctk.CTkFont(size=13), height=36, width=100,
            corner_radius=8, fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._cancel_profile_edit,
        ).grid(row=0, column=1)

        self._refresh_profile_list()

    def _on_gnd_toggle(self):
        if self._gnd_enabled_var.get():
            self._gnd_frame.grid()
            self._gnd_toggle.configure(text="włączony")

            defaults = {
                "gnd_current": "25.0", "gnd_hi_limit": "100",
                "gnd_lo_limit": "0", "gnd_dwell": "1.0",
                "gnd_offset": "0",
            }
            for key, val in defaults.items():
                if not self._gnd_entries[key].get():
                    self._gnd_entries[key].insert(0, val)
        else:
            self._gnd_frame.grid_remove()
            self._gnd_toggle.configure(text="wyłączony")

    def _refresh_profile_list(self):
        for widget in self._profile_listbox.winfo_children():
            widget.destroy()

        profiles = load_config().get("profiles", {})

        for key in sorted(profiles.keys()):
            is_sel = key == self._selected_profile_key
            ctk.CTkButton(
                self._profile_listbox,
                text=key,
                font=ctk.CTkFont(size=13),
                height=34, corner_radius=6,
                fg_color=COLORS["primary"] if is_sel else "transparent",
                hover_color="#005a9e",
                border_width=1,
                border_color=COLORS["primary"] if is_sel else COLORS["border"],
                text_color=COLORS["text"],
                anchor="w",
                command=lambda k=key: self._select_profile(k),
            ).pack(fill="x", pady=2)

        if profiles and self._selected_profile_key is None and not self._new_profile_mode:
            self._select_profile(sorted(profiles.keys())[0])

    def _select_profile(self, key):
        self._new_profile_mode = False
        self._selected_profile_key = key

        profile = load_config().get("profiles", {}).get(key, {})

        self._prof_entries["key"].configure(state="normal")
        self._prof_entries["key"].delete(0, "end")
        self._prof_entries["key"].insert(0, key)
        self._prof_entries["key"].configure(state="disabled")

        for field_key in ["name", "type", "voltage", "hi_limit",
                          "lo_limit", "ramp", "dwell"]:
            entry = self._prof_entries[field_key]
            entry.delete(0, "end")
            entry.insert(0, str(profile.get(field_key, "")))

        freq = str(profile.get("frequency", 0))
        self._freq_var.set("50 Hz (0)" if freq == "0" else "60 Hz (1)")

        gnd = profile.get("ground_bond")

        if gnd:
            self._gnd_enabled_var.set(True)
            self._gnd_frame.grid()
            self._gnd_toggle.configure(text="włączony")

            mapping = {
                "gnd_current": "current",
                "gnd_hi_limit": "hi_limit",
                "gnd_lo_limit": "lo_limit",
                "gnd_dwell": "dwell",
                "gnd_offset": "offset",
            }
            defaults = {
                "gnd_current": 25.0, "gnd_hi_limit": 100,
                "gnd_lo_limit": 0, "gnd_dwell": 1.0,
                "gnd_offset": 0,
            }
            for ui_key, cfg_key in mapping.items():
                self._gnd_entries[ui_key].delete(0, "end")
                self._gnd_entries[ui_key].insert(
                    0, str(gnd.get(cfg_key, defaults[ui_key]))
                )

            gnd_freq = str(gnd.get("frequency", 1))
            self._gnd_freq_var.set("60 Hz (1)" if gnd_freq == "1" else "50 Hz (0)")
        else:
            self._gnd_enabled_var.set(False)
            self._gnd_frame.grid_remove()
            self._gnd_toggle.configure(text="wyłączony")

        self._refresh_profile_list()

    def _new_profile(self):
        self._selected_profile_key = None
        self._new_profile_mode = True

        key_entry = self._prof_entries["key"]
        key_entry.configure(state="normal")
        key_entry.delete(0, "end")

        for field_key in ["name", "type", "voltage", "hi_limit",
                          "lo_limit", "ramp", "dwell"]:
            self._prof_entries[field_key].delete(0, "end")

        self._freq_var.set("50 Hz (0)")

        self._gnd_enabled_var.set(False)
        self._gnd_frame.grid_remove()
        self._gnd_toggle.configure(text="wyłączony")

        for entry in self._gnd_entries.values():
            entry.delete(0, "end")

        self._refresh_profile_list()

        key_entry.focus()
        self._show_profile_status(
            "Wprowadź dane nowego profilu i kliknij Zapisz", COLORS["muted"]
        )

    def _delete_profile(self):
        if not self._selected_profile_key:
            self._show_profile_status("⚠ Zaznacz profil do usunięcia", COLORS["warning"])
            return

        deleted = self._selected_profile_key
        config = load_config()

        removed = config.get("profiles", {}).pop(deleted, None)

        # Prefiksy SN wskazujące na usunięty profil zostawiłyby "martwe"
        # mapowanie — operator dostawałby "Nieznany SN" bez wyjaśnienia.
        orphaned = [
            prefix for prefix, key in config.get("sn_prefix_map", {}).items()
            if key == deleted
        ]

        if not save_config(config):
            self._show_profile_status(
                "❌ Nie udało się zapisać konfiguracji — sprawdź logs/app.log",
                COLORS["fail"],
            )
            return

        audit(self.actor, "PROFILE_DELETE",
              f"key={deleted} | dane={removed} | osierocone prefiksy={orphaned}")

        self._selected_profile_key = None

        for field_key in ["name", "type", "voltage", "hi_limit",
                          "lo_limit", "ramp", "dwell"]:
            self._prof_entries[field_key].delete(0, "end")

        self._prof_entries["key"].configure(state="normal")
        self._prof_entries["key"].delete(0, "end")

        self._refresh_profile_list()

        msg = f"🗑 Usunięto profil '{deleted}'"
        if orphaned:
            msg += f" — UWAGA: prefiksy bez profilu: {', '.join(orphaned)}"

        self._show_profile_status(
            msg, COLORS["warning"] if orphaned else COLORS["muted"]
        )

    def _cancel_profile_edit(self):
        if self._selected_profile_key:
            self._select_profile(self._selected_profile_key)
        else:
            for field_key in ["name", "type", "voltage", "hi_limit",
                              "lo_limit", "ramp", "dwell"]:
                self._prof_entries[field_key].delete(0, "end")
            self._prof_entries["key"].configure(state="normal")
            self._prof_entries["key"].delete(0, "end")

    def _read_float(self, entry, label: str) -> float:
        raw = entry.get().strip().replace(",", ".")
        if not raw:
            raise ValueError(f"{label}: pole puste")
        return float(raw)

    def _save_profile(self):
        try:
            key_entry = self._prof_entries["key"]
            key_entry.configure(state="normal")
            new_key = key_entry.get().strip()

            if not new_key:
                self._show_profile_status(
                    "⚠ Klucz profilu nie może być pusty", COLORS["fail"]
                )
                return

            name = self._prof_entries["name"].get().strip()
            ptype = self._prof_entries["type"].get().strip().upper()

            voltage = self._read_float(self._prof_entries["voltage"], "Napięcie")
            hi_limit = self._read_float(self._prof_entries["hi_limit"], "HI limit")
            lo_limit = self._read_float(self._prof_entries["lo_limit"], "LO limit")
            ramp = self._read_float(self._prof_entries["ramp"], "Ramp")
            dwell = self._read_float(self._prof_entries["dwell"], "Dwell")
            frequency = 0 if "50" in self._freq_var.get() else 1

            errors = []

            for field, value in [
                ("voltage", voltage), ("hi_limit", hi_limit),
                ("lo_limit", lo_limit), ("ramp", ramp), ("dwell", dwell),
            ]:
                problem = check_range(field, value)
                if problem:
                    errors.append(problem)

            if hi_limit <= lo_limit:
                errors.append("HI limit musi być większy niż LO limit")

            if errors:
                self._show_profile_status("⚠ " + " | ".join(errors), COLORS["fail"])
                return

            # ── Ground Bond ───────────────────────────────────────────────
            ground_bond = None

            if self._gnd_enabled_var.get():
                gc = self._read_float(self._gnd_entries["gnd_current"], "Prąd GND")
                g_hi = self._read_float(self._gnd_entries["gnd_hi_limit"], "HI GND")
                g_lo = self._read_float(self._gnd_entries["gnd_lo_limit"], "LO GND")
                g_dw = self._read_float(self._gnd_entries["gnd_dwell"], "Dwell GND")
                g_off = self._read_float(self._gnd_entries["gnd_offset"], "Offset GND")
                g_frq = 0 if "50" in self._gnd_freq_var.get() else 1

                gnd_errors = []

                for field, value in [
                    ("gnd_current", gc), ("gnd_hi_limit", g_hi),
                    ("gnd_lo_limit", g_lo), ("gnd_dwell", g_dw),
                    ("gnd_offset", g_off),
                ]:
                    problem = check_range(field, value)
                    if problem:
                        gnd_errors.append(problem)

                if g_hi <= g_lo:
                    gnd_errors.append("HI limit GND musi być większy niż LO")

                if gnd_errors:
                    self._show_profile_status(
                        "⚠ " + " | ".join(gnd_errors), COLORS["fail"]
                    )
                    return

                ground_bond = {
                    "current": gc,
                    "hi_limit": g_hi,
                    "lo_limit": g_lo,
                    "dwell": g_dw,
                    "offset": g_off,
                    "frequency": g_frq,
                }

            config = load_config()
            previous = config.get("profiles", {}).get(
                self._selected_profile_key or new_key
            )

            if self._selected_profile_key and self._selected_profile_key != new_key:
                config.get("profiles", {}).pop(self._selected_profile_key, None)

                for prefix, pk in list(config.get("sn_prefix_map", {}).items()):
                    if pk == self._selected_profile_key:
                        config["sn_prefix_map"][prefix] = new_key

            new_profile = {
                "name": name,
                "type": ptype,
                "voltage": voltage,
                "hi_limit": hi_limit,
                "lo_limit": lo_limit,
                "ramp": ramp,
                "dwell": dwell,
                "frequency": frequency,
                "ground_bond": ground_bond,
            }

            config.setdefault("profiles", {})[new_key] = new_profile

            if not save_config(config):
                self._show_profile_status(
                    "❌ Nie udało się zapisać konfiguracji — sprawdź logs/app.log",
                    COLORS["fail"],
                )
                return

            audit(
                self.actor, "PROFILE_SAVE",
                f"key={new_key} | przed={previous} | po={new_profile}",
            )

            # Ostrzeżenie o braku portu ESP przy profilu z GND — lepiej teraz
            # niż w trakcie testu.
            warning = ""
            if ground_bond and not config.get("serial", {}).get("relay_port"):
                warning = (
                    " ⚠ UWAGA: brak portu ESP w zakładce Relay — "
                    "test z Ground Bond zostanie zablokowany."
                )

            self._selected_profile_key = new_key
            self._new_profile_mode = False
            key_entry.configure(state="disabled")
            self._refresh_profile_list()

            self._show_profile_status(
                f"✔ Profil '{new_key}' zapisany{warning}",
                COLORS["warning"] if warning else COLORS["success"],
            )

        except ValueError as e:
            self._show_profile_status(f"⚠ {e}", COLORS["fail"])

    def _show_profile_status(self, msg, color):
        self._profile_status.configure(text=msg, text_color=color)
        self.after(6000, lambda: self._profile_status.configure(text=""))

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: SN Prefix Map
    # ══════════════════════════════════════════════════════════════════════
    def _build_sn_map_tab(self):
        tab = self.tabs.tab("SN Prefix Map")
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab,
            text="Mapowanie prefiksów SN na profile "
                 "(dopasowanie po najdłuższym pasującym prefiksie)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(8, 12))

        self._sn_scroll = ctk.CTkScrollableFrame(
            tab, fg_color=COLORS["surface"], corner_radius=8
        )
        self._sn_scroll.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(0, 12))
        self._sn_scroll.grid_columnconfigure(0, weight=1)
        self._sn_scroll.grid_columnconfigure(1, weight=1)

        self._refresh_sn_list()

        add_frame = ctk.CTkFrame(tab, fg_color="transparent")
        add_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
        add_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            add_frame, text="Nowy prefiks:",
            font=ctk.CTkFont(size=12), text_color=COLORS["muted"],
        ).grid(row=0, column=0, padx=(0, 8))

        self._new_prefix_entry = ctk.CTkEntry(
            add_frame, font=ctk.CTkFont(size=13),
            height=34, width=140, corner_radius=8,
            border_color=COLORS["border"],
            placeholder_text="np. F10001 lub 084999",
        )
        self._new_prefix_entry.grid(row=0, column=1, sticky="w", padx=(0, 8))

        profile_keys = list(load_config().get("profiles", {}).keys())
        self._new_prefix_profile_var = ctk.StringVar(
            value=profile_keys[0] if profile_keys else ""
        )

        self._new_prefix_profile_menu = ctk.CTkOptionMenu(
            add_frame,
            values=profile_keys or ["(brak profili)"],
            variable=self._new_prefix_profile_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=140,
        )
        self._new_prefix_profile_menu.grid(row=0, column=2, padx=(0, 8))

        ctk.CTkButton(
            add_frame, text="➕ Dodaj",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=34, width=100, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._add_sn_prefix,
        ).grid(row=0, column=3)

        self._sn_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"],
            wraplength=700, justify="left",
        )
        self._sn_status.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _refresh_sn_list(self):
        for widget in self._sn_scroll.winfo_children():
            widget.destroy()

        config = load_config()
        sn_map = config.get("sn_prefix_map", {})
        profiles = config.get("profiles", {})

        for col, hdr in enumerate(["Prefiks SN", "Profil", ""]):
            ctk.CTkLabel(
                self._sn_scroll, text=hdr,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COLORS["muted"],
            ).grid(row=0, column=col, sticky="w", padx=8, pady=(4, 2))

        for i, (prefix, profile_key) in enumerate(sorted(sn_map.items())):
            exists = profile_key in profiles

            ctk.CTkLabel(
                self._sn_scroll, text=prefix,
                font=ctk.CTkFont(size=13),
                text_color=COLORS["text"],
            ).grid(row=i + 1, column=0, sticky="w", padx=8, pady=2)

            ctk.CTkLabel(
                self._sn_scroll,
                text=profile_key if exists else f"{profile_key} ⚠ brak profilu",
                font=ctk.CTkFont(size=13),
                text_color=COLORS["primary"] if exists else COLORS["fail"],
            ).grid(row=i + 1, column=1, sticky="w", padx=8, pady=2)

            ctk.CTkButton(
                self._sn_scroll, text="🗑",
                width=32, height=26,
                font=ctk.CTkFont(size=12),
                fg_color="transparent",
                hover_color=COLORS["fail"],
                border_width=1, border_color=COLORS["border"],
                command=lambda p=prefix: self._delete_sn_prefix(p),
            ).grid(row=i + 1, column=2, padx=(8, 4), pady=2)

    def _add_sn_prefix(self):
        prefix = self._new_prefix_entry.get().strip()
        profile_key = self._new_prefix_profile_var.get()

        if len(prefix) < 4 or len(prefix) > 12:
            self._show_sn_status("⚠ Prefiks musi mieć 4–12 znaków", COLORS["fail"])
            return

        config = load_config()

        if profile_key not in config.get("profiles", {}):
            self._show_sn_status(
                f"⚠ Profil '{profile_key}' nie istnieje", COLORS["fail"]
            )
            return

        if prefix in config.get("sn_prefix_map", {}):
            self._show_sn_status(f"⚠ Prefiks {prefix} już istnieje", COLORS["warning"])
            return

        # Ostrzeżenie o zachodzeniu prefiksów — wygrywa dłuższy, ale inżynier
        # powinien o tym wiedzieć świadomie.
        overlaps = [
            p for p in config.get("sn_prefix_map", {})
            if p.upper().startswith(prefix.upper())
            or prefix.upper().startswith(str(p).upper())
        ]

        config.setdefault("sn_prefix_map", {})[prefix] = profile_key

        if not save_config(config):
            self._show_sn_status("❌ Nie udało się zapisać konfiguracji",
                                 COLORS["fail"])
            return

        audit(self.actor, "SN_PREFIX_ADD", f"{prefix} -> {profile_key}")

        self._new_prefix_entry.delete(0, "end")
        self._refresh_sn_list()

        msg = f"✔ Dodano {prefix} → {profile_key}"
        if overlaps:
            msg += (f" | uwaga: zachodzi z {', '.join(overlaps)} — "
                    "wygrywa dłuższy prefiks")

        self._show_sn_status(msg, COLORS["warning"] if overlaps else COLORS["success"])

    def _delete_sn_prefix(self, prefix):
        config = load_config()
        removed = config.get("sn_prefix_map", {}).pop(prefix, None)

        if not save_config(config):
            self._show_sn_status("❌ Nie udało się zapisać konfiguracji",
                                 COLORS["fail"])
            return

        audit(self.actor, "SN_PREFIX_DELETE", f"{prefix} -> {removed}")

        self._refresh_sn_list()
        self._show_sn_status(f"🗑 Usunięto prefiks {prefix}", COLORS["muted"])

    def _show_sn_status(self, msg, color):
        self._sn_status.configure(text=msg, text_color=color)
        self.after(6000, lambda: self._sn_status.configure(text=""))

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
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(8, 12))

        self._hrid_scroll = ctk.CTkScrollableFrame(
            tab, fg_color=COLORS["surface"], corner_radius=8
        )
        self._hrid_scroll.grid(row=1, column=0, columnspan=4,
                               sticky="nsew", pady=(0, 12))
        self._hrid_scroll.grid_columnconfigure(0, weight=1)
        self._hrid_scroll.grid_columnconfigure(1, weight=1)

        self._refresh_hrid_list()

        add_frame = ctk.CTkFrame(tab, fg_color="transparent")
        add_frame.grid(row=2, column=0, columnspan=4, sticky="ew")

        for col, (lbl, attr, ph, w) in enumerate([
            ("HRID:", "_new_hrid_entry", "np. 12345678", 120),
            ("Imię i nazwisko:", "_new_hrid_name_entry", "Jan Kowalski", 160),
        ]):
            ctk.CTkLabel(
                add_frame, text=lbl,
                font=ctk.CTkFont(size=12), text_color=COLORS["muted"],
            ).grid(row=0, column=col * 2, padx=(0, 6))

            e = ctk.CTkEntry(
                add_frame, font=ctk.CTkFont(size=13),
                height=34, width=w, corner_radius=8,
                border_color=COLORS["border"],
                placeholder_text=ph,
            )
            e.grid(row=0, column=col * 2 + 1, padx=(0, 8))
            setattr(self, attr, e)

        self._new_hrid_role_var = ctk.StringVar(value="operator")
        ctk.CTkOptionMenu(
            add_frame,
            values=["operator", "engineer"],
            variable=self._new_hrid_role_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=120,
        ).grid(row=0, column=4, padx=(0, 8))

        ctk.CTkButton(
            add_frame, text="➕ Dodaj",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=34, width=100, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._add_hrid_user,
        ).grid(row=0, column=5)

        self._hrid_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"],
        )
        self._hrid_status.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _refresh_hrid_list(self):
        for widget in self._hrid_scroll.winfo_children():
            widget.destroy()

        users = load_config().get("users", {})

        for col, hdr in enumerate(["HRID", "Imię i nazwisko", "Rola", ""]):
            ctk.CTkLabel(
                self._hrid_scroll, text=hdr,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COLORS["muted"],
            ).grid(row=0, column=col, sticky="w", padx=8, pady=(4, 2))

        for i, (hrid, data) in enumerate(sorted(users.items())):
            ctk.CTkLabel(
                self._hrid_scroll, text=hrid,
                font=ctk.CTkFont(size=13), text_color=COLORS["text"],
            ).grid(row=i + 1, column=0, sticky="w", padx=8, pady=2)

            ctk.CTkLabel(
                self._hrid_scroll, text=data.get("name", ""),
                font=ctk.CTkFont(size=13), text_color=COLORS["text"],
            ).grid(row=i + 1, column=1, sticky="w", padx=8, pady=2)

            role = data.get("role", "operator")
            ctk.CTkLabel(
                self._hrid_scroll, text=role,
                font=ctk.CTkFont(size=13),
                text_color=COLORS["primary"] if role == "engineer" else COLORS["muted"],
            ).grid(row=i + 1, column=2, sticky="w", padx=8, pady=2)

            ctk.CTkButton(
                self._hrid_scroll, text="🗑",
                width=32, height=26,
                font=ctk.CTkFont(size=12),
                fg_color="transparent",
                hover_color=COLORS["fail"],
                border_width=1, border_color=COLORS["border"],
                command=lambda h=hrid: self._delete_hrid_user(h),
            ).grid(row=i + 1, column=3, padx=(8, 4), pady=2)

    def _add_hrid_user(self):
        hrid = self._new_hrid_entry.get().strip()
        name = self._new_hrid_name_entry.get().strip()
        role = self._new_hrid_role_var.get()

        if not hrid or not name:
            self._show_hrid_status("⚠ Wypełnij HRID i imię", COLORS["fail"])
            return

        config = load_config()

        if hrid in config.get("users", {}):
            self._show_hrid_status(f"⚠ HRID {hrid} już istnieje", COLORS["warning"])
            return

        config.setdefault("users", {})[hrid] = {"name": name, "role": role}

        if not save_config(config):
            self._show_hrid_status("❌ Nie udało się zapisać konfiguracji",
                                   COLORS["fail"])
            return

        audit(self.actor, "USER_ADD", f"hrid={hrid} | name={name} | role={role}")

        self._new_hrid_entry.delete(0, "end")
        self._new_hrid_name_entry.delete(0, "end")
        self._refresh_hrid_list()
        self._show_hrid_status(f"✔ Dodano {name} ({hrid})", COLORS["success"])

    def _delete_hrid_user(self, hrid):
        config = load_config()
        removed = config.get("users", {}).pop(hrid, None)

        if not save_config(config):
            self._show_hrid_status("❌ Nie udało się zapisać konfiguracji",
                                   COLORS["fail"])
            return

        audit(self.actor, "USER_DELETE", f"hrid={hrid} | dane={removed}")

        self._refresh_hrid_list()
        self._show_hrid_status(f"🗑 Usunięto {hrid}", COLORS["muted"])

    def _show_hrid_status(self, msg, color):
        self._hrid_status.configure(text=msg, text_color=color)
        self.after(6000, lambda: self._hrid_status.configure(text=""))

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Konfiguracja portu + parametry testu
    # ══════════════════════════════════════════════════════════════════════
    def _build_port_tab(self):
        tab = self.tabs.tab("Konfiguracja portu")
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            tab, text="Konfiguracja RS-232 (tester)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(8, 16))

        serial_cfg = load_config().get("serial", {})

        ctk.CTkLabel(
            tab, text="Port COM:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=180,
        ).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)

        port_frame = ctk.CTkFrame(tab, fg_color="transparent")
        port_frame.grid(row=1, column=1, sticky="w", pady=6)

        current_port = serial_cfg.get("port", "COM11")
        self._port_var = ctk.StringVar(value=current_port)

        self._port_menu = ctk.CTkOptionMenu(
            port_frame,
            values=self._port_values(current_port),
            variable=self._port_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=140,
        )
        self._port_menu.grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            port_frame, text="↺ Odśwież",
            font=ctk.CTkFont(size=12),
            height=32, width=100, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._refresh_ports,
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            tab, text="Baudrate:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=180,
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
            width=140,
        ).grid(row=2, column=1, sticky="w", pady=6)

        ctk.CTkLabel(
            tab, text="Timeout (s):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=180,
        ).grid(row=3, column=0, sticky="w", padx=(0, 12), pady=6)

        self._timeout_entry = ctk.CTkEntry(
            tab, font=ctk.CTkFont(size=13),
            height=36, width=140, corner_radius=8,
            border_color=COLORS["border"],
        )
        self._timeout_entry.insert(0, str(serial_cfg.get("timeout", 3)))
        self._timeout_entry.grid(row=3, column=1, sticky="w", pady=6)

        # ── Parametry testu ───────────────────────────────────────────────
        ctk.CTkFrame(tab, fg_color=COLORS["border"], height=1).grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(16, 12)
        )

        ctk.CTkLabel(
            tab, text="Parametry czasowe testu",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 10))

        hipot_cfg = load_config().get("hipot", {})

        self._hipot_entries = {}

        hipot_fields = [
            ("Margines odczytu wyniku (s):", "result_margin_s", "30.0",
             "Doliczany do ramp+dwell. Limit awaryjny, nie skrócenie."),
            ("Odstęp odpytywania RD (s):", "result_poll_interval_s", "0.5", ""),
            ("Opóźnienie przed relay (s):", "relay_switch_delay_s", "1.0",
             "Odczekanie po odczycie wyniku, zanim ruszą styki."),
        ]

        row = 6
        for label, key, default, hint in hipot_fields:
            ctk.CTkLabel(
                tab, text=label,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["muted"], anchor="w", width=220,
            ).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)

            entry = ctk.CTkEntry(
                tab, font=ctk.CTkFont(size=13),
                height=32, width=100, corner_radius=8,
                border_color=COLORS["border"],
            )
            entry.insert(0, str(hipot_cfg.get(key, default)))
            entry.grid(row=row, column=1, sticky="w", pady=4)
            self._hipot_entries[key] = entry

            if hint:
                ctk.CTkLabel(
                    tab, text=hint,
                    font=ctk.CTkFont(size=10),
                    text_color=COLORS["muted"], anchor="w",
                ).grid(row=row, column=2, sticky="w", padx=(10, 0))

            row += 1

        self._require_relay_var = ctk.BooleanVar(
            value=bool(hipot_cfg.get("require_relay_for_gnd", True))
        )
        ctk.CTkSwitch(
            tab,
            text="Blokuj test z Ground Bond bez skonfigurowanego portu ESP",
            variable=self._require_relay_var,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text"],
            progress_color=COLORS["primary"],
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 4))
        row += 1

        ctk.CTkLabel(
            tab, text="Kolejność pól wyniku GND:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=220,
        ).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)

        self._gnd_order_var = ctk.StringVar(
            value=str(hipot_cfg.get("gnd_field_order", "auto"))
        )
        ctk.CTkOptionMenu(
            tab,
            values=["auto", "current_first", "resistance_first"],
            variable=self._gnd_order_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=170,
        ).grid(row=row, column=1, sticky="w", pady=6)

        ctk.CTkLabel(
            tab,
            text="auto = rozpoznanie po zadanym prądzie GND",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["muted"], anchor="w",
        ).grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1

        self._port_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"],
            wraplength=600, justify="left",
        )
        self._port_status.grid(row=row, column=0, columnspan=3,
                               sticky="w", pady=(12, 0))
        row += 1

        ctk.CTkButton(
            tab, text="💾 Zapisz konfigurację",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38, width=220, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._save_port_config,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))

    @staticmethod
    def _port_values(current: str | None) -> list[str]:
        """
        Lista portów z zachowaniem aktualnie wybranego, nawet gdy chwilowo
        nie jest widoczny w systemie.
        """
        ports = [p.device for p in serial.tools.list_ports.comports()]

        if current and current not in ports:
            ports.append(f"{current}")

        return ports or ["(brak portów)"]

    def _refresh_ports(self):
        current = self._port_var.get()
        values = self._port_values(current)

        self._port_menu.configure(values=values)

        # Nie nadpisujemy świadomego wyboru inżyniera — poprzednia wersja
        # ustawiała ports[0] przy każdym odświeżeniu.
        if current in values:
            self._port_var.set(current)
        else:
            self._port_var.set(values[0])

        self._show_port_status(f"Znaleziono porty: {', '.join(values)}",
                               COLORS["muted"])

    def _save_port_config(self):
        try:
            timeout = int(float(self._timeout_entry.get().strip().replace(",", ".")))

            if timeout < 1 or timeout > 60:
                self._show_port_status("⚠ Timeout musi być 1–60 s", COLORS["fail"])
                return

            hipot_values = {}

            for key, entry in self._hipot_entries.items():
                raw = entry.get().strip().replace(",", ".")
                value = float(raw)

                if value < 0:
                    self._show_port_status(
                        f"⚠ {key}: wartość nie może być ujemna", COLORS["fail"]
                    )
                    return

                hipot_values[key] = value

            if hipot_values.get("result_margin_s", 30.0) < 5.0:
                self._show_port_status(
                    "⚠ Margines odczytu wyniku poniżej 5 s jest ryzykowny — "
                    "tester może nie zdążyć zapisać wyniku.",
                    COLORS["fail"],
                )
                return

            config = load_config()

            serial_cfg = config.setdefault("serial", {})
            previous_serial = dict(serial_cfg)

            serial_cfg.update({
                "port": self._port_var.get(),
                "baudrate": int(self._baud_var.get()),
                "timeout": timeout,
            })

            hipot_cfg = config.setdefault("hipot", {})
            previous_hipot = dict(hipot_cfg)

            hipot_cfg.update(hipot_values)
            hipot_cfg["require_relay_for_gnd"] = bool(self._require_relay_var.get())
            hipot_cfg["gnd_field_order"] = self._gnd_order_var.get()

            if not save_config(config):
                self._show_port_status("❌ Nie udało się zapisać konfiguracji",
                                       COLORS["fail"])
                return

            audit(self.actor, "PORT_CONFIG_SAVE",
                  f"serial przed={previous_serial} po={serial_cfg}")
            audit(self.actor, "HIPOT_PARAMS_SAVE",
                  f"hipot przed={previous_hipot} po={hipot_cfg}")

            self._show_port_status("✔ Konfiguracja zapisana", COLORS["success"])

        except ValueError:
            self._show_port_status("⚠ Nieprawidłowa wartość liczbowa",
                                   COLORS["fail"])

    def _show_port_status(self, msg, color):
        self._port_status.configure(text=msg, text_color=color)
        self.after(6000, lambda: self._port_status.configure(text=""))

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Relay (ESP)
    # ══════════════════════════════════════════════════════════════════════
    def _build_relay_tab(self):
        tab = self.tabs.tab("Relay (ESP)")
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(7, weight=1)

        ctk.CTkLabel(
            tab, text="Konfiguracja Relay ESP8266",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(8, 16))

        serial_cfg = load_config().get("serial", {})

        ctk.CTkLabel(
            tab, text="Port COM (ESP):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=160,
        ).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)

        relay_port_frame = ctk.CTkFrame(tab, fg_color="transparent")
        relay_port_frame.grid(row=1, column=1, sticky="w", pady=6)

        current_relay = serial_cfg.get("relay_port") or ""
        self._relay_port_var = ctk.StringVar(value=current_relay or "(nie ustawiony)")

        self._relay_port_menu = ctk.CTkOptionMenu(
            relay_port_frame,
            values=self._port_values(current_relay or None),
            variable=self._relay_port_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=140,
        )
        self._relay_port_menu.grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            relay_port_frame, text="↺ Odśwież",
            font=ctk.CTkFont(size=12),
            height=32, width=100, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._refresh_relay_ports,
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            tab, text="Baudrate (ESP):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w", width=160,
        ).grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)

        self._relay_baud_var = ctk.StringVar(
            value=str(serial_cfg.get("relay_baudrate", 115200))
        )
        ctk.CTkOptionMenu(
            tab,
            values=["9600", "19200", "57600", "115200"],
            variable=self._relay_baud_var,
            fg_color=COLORS["card"],
            button_color=COLORS["primary"],
            button_hover_color="#005a9e",
            font=ctk.CTkFont(size=13),
            width=140,
        ).grid(row=2, column=1, sticky="w", pady=6)

        ctk.CTkButton(
            tab, text="💾 Zapisz konfigurację relay",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38, width=220, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._save_relay_config,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 12))

        ctk.CTkLabel(
            tab,
            text="⚠ Przyciski poniżej fizycznie przełączają styki przekaźnika. "
                 "Są zablokowane w trakcie testu.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["warning"],
            wraplength=700, justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))

        relay_btns = [
            ("🔌 PING", "ping", True),
            ("→ PE", "pe", False),
            ("→ HIPOT", "hipot", False),
            ("❓ STATUS", "status", False),
        ]
        for text, cmd, primary in relay_btns:
            ctk.CTkButton(
                btn_frame, text=text,
                font=ctk.CTkFont(size=12),
                height=34, width=120, corner_radius=8,
                fg_color=COLORS["primary"] if primary else "transparent",
                border_width=0 if primary else 1,
                border_color=COLORS["border"],
                hover_color="#005a9e" if primary else COLORS["bg"],
                command=lambda c=cmd: self._relay_action(c),
            ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            tab, text="Log relay:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=6, column=0, sticky="w", pady=(4, 4))

        self._relay_log = ctk.CTkTextbox(
            tab,
            font=ctk.CTkFont(family="Courier", size=12),
            fg_color=COLORS["surface"],
            text_color=COLORS["text"],
            corner_radius=8,
            height=220,
        )
        self._relay_log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self._relay_log.configure(state="disabled")

        self._relay_status_lbl = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"],
            wraplength=700, justify="left",
        )
        self._relay_status_lbl.grid(row=8, column=0, columnspan=2,
                                    sticky="w", pady=(6, 0))

    def _refresh_relay_ports(self):
        current = self._relay_port_var.get()
        values = self._port_values(current if current.startswith("COM") else None)

        self._relay_port_menu.configure(values=values)

        if current in values:
            self._relay_port_var.set(current)
        else:
            self._relay_port_var.set(values[0])

    def _save_relay_config(self):
        port = self._relay_port_var.get()

        if not port or not port.upper().startswith("COM"):
            self._relay_status_lbl.configure(
                text="⚠ Wybierz prawidłowy port COM dla ESP",
                text_color=COLORS["fail"],
            )
            return

        config = load_config()
        serial_cfg = config.setdefault("serial", {})
        previous = dict(serial_cfg)

        serial_cfg["relay_port"] = port
        serial_cfg["relay_baudrate"] = int(self._relay_baud_var.get())

        if not save_config(config):
            self._relay_status_lbl.configure(
                text="❌ Nie udało się zapisać konfiguracji",
                text_color=COLORS["fail"],
            )
            return

        audit(self.actor, "RELAY_CONFIG_SAVE",
              f"przed={previous} po={serial_cfg}")

        self._relay_status_lbl.configure(
            text="✔ Konfiguracja relay zapisana", text_color=COLORS["success"]
        )
        self.after(6000, lambda: self._relay_status_lbl.configure(text=""))

    def _relay_log_write(self, msg: str):
        self._relay_log.configure(state="normal")
        self._relay_log.insert("end", msg + "\n")
        self._relay_log.see("end")
        self._relay_log.configure(state="disabled")

    def _relay_action(self, action: str):
        if self._blocked_by_test():
            return

        audit(self.actor, "RELAY_MANUAL", f"akcja={action}")

        threading.Thread(
            target=self._do_relay_action, args=(action,), daemon=True
        ).start()

    def _do_relay_action(self, action: str):
        from relay_controller import RelayController, RelayError

        serial_cfg = load_config().get("serial", {})
        port = serial_cfg.get("relay_port") or self._relay_port_var.get()
        baud = int(serial_cfg.get("relay_baudrate", 115200))

        self.after(0, self._relay_log_write,
                   f"── {action.upper()} ── port: {port} | baud: {baud}")

        if not port or not str(port).upper().startswith("COM"):
            self.after(0, self._relay_log_write,
                       "❌ Port ESP nie jest skonfigurowany")
            return

        relay = RelayController(port=port, baudrate=baud)

        try:
            relay.connect()
            self.after(0, self._relay_log_write, "✔ Połączono z ESP")

            # Ostatnie sprawdzenie tuż przed wysłaniem komendy — test mógł
            # ruszyć między kliknięciem a wykonaniem wątku.
            if runtime_state.test_in_progress():
                self.after(0, self._relay_log_write,
                           "⛔ Test wystartował — przerywam akcję.")
                return

            if action == "ping":
                ok = relay.ping()
                self.after(0, self._relay_log_write,
                           "PING → PONG ✔" if ok else "PING → brak odpowiedzi ✘")

            elif action == "pe":
                relay.set_pe()
                self.after(0, self._relay_log_write, "✔ Relay przełączony → PE")

            elif action == "hipot":
                relay.set_hipot()
                self.after(0, self._relay_log_write, "✔ Relay przełączony → HIPOT")

            elif action == "status":
                state = relay.get_status()
                self.after(0, self._relay_log_write, f"STATUS → {state}")

        except RelayError as e:
            self.after(0, self._relay_log_write, f"❌ RelayError: {e}")

        except Exception as e:
            log.exception("Błąd akcji relay")
            self.after(0, self._relay_log_write, f"❌ Błąd: {e}")

        finally:
            # Panel nie zostawia przekaźnika na PE — to pozycja niebezpieczna
            # dla kolejnego testu HiPot.
            if action == "pe":
                try:
                    relay.safe_return_to_hipot()
                    self.after(0, self._relay_log_write,
                               "↩ Powrót na HIPOT po teście ręcznym")
                except Exception as e:
                    self.after(0, self._relay_log_write,
                               f"⚠ Powrót na HIPOT nieudany: {e}")

            relay.disconnect()
            self.after(0, self._relay_log_write,
                       "── koniec ──────────────────────────────────")

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Diagnostyka
    # ══════════════════════════════════════════════════════════════════════
    def _build_diagnostics_tab(self):
        tab = self.tabs.tab("Diagnostyka")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            tab, text="Diagnostyka połączenia RS-232",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w", pady=(8, 6))

        ctk.CTkLabel(
            tab,
            text="⚠ Komendy idą bezpośrednio do testera. Zablokowane w trakcie testu.\n"
                 "Wskazówka: odpowiedzi na SA? wpisz do config.json → "
                 "hipot.status_busy_tokens / status_idle_tokens, "
                 "żeby aplikacja rozpoznawała stan testera.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["warning"],
            justify="left", wraplength=760,
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="w", pady=(0, 8))

        ctk.CTkButton(
            btn_frame, text="🔌 Test połączenia (RESET)",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38, width=220, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._run_connection_test,
        ).grid(row=0, column=0, padx=(0, 12))

        ctk.CTkButton(
            btn_frame, text="📋 Wyślij komendę ręcznie",
            font=ctk.CTkFont(size=13),
            height=38, width=200, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._send_manual_command,
        ).grid(row=0, column=1, padx=(0, 12))

        ctk.CTkButton(
            btn_frame, text="🗑 Wyczyść log",
            font=ctk.CTkFont(size=13),
            height=38, width=130, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._clear_log,
        ).grid(row=0, column=2)

        manual_frame = ctk.CTkFrame(tab, fg_color="transparent")
        manual_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        manual_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            manual_frame, text="Komenda:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        cmd_row = ctk.CTkFrame(manual_frame, fg_color="transparent")
        cmd_row.grid(row=1, column=0, sticky="ew")
        cmd_row.grid_columnconfigure(0, weight=1)

        self._manual_cmd_entry = ctk.CTkEntry(
            cmd_row, font=ctk.CTkFont(size=13),
            height=36, corner_radius=8,
            border_color=COLORS["border"],
            placeholder_text="np. *IDN? lub SA? lub LS 2?",
        )
        self._manual_cmd_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._manual_cmd_entry.bind("<Return>", lambda e: self._send_manual_command())

        ctk.CTkButton(
            cmd_row, text="Wyślij",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36, width=80, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._send_manual_command,
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            tab, text="Log komunikacji:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=4, column=0, sticky="w", pady=(8, 4))

        self._diag_log = ctk.CTkTextbox(
            tab,
            font=ctk.CTkFont(family="Courier", size=12),
            fg_color=COLORS["surface"],
            text_color=COLORS["text"],
            corner_radius=8,
            height=260,
        )
        self._diag_log.grid(row=5, column=0, sticky="nsew")
        self._diag_log.configure(state="disabled")

    def _log(self, msg: str):
        self._diag_log.configure(state="normal")
        self._diag_log.insert("end", msg + "\n")
        self._diag_log.see("end")
        self._diag_log.configure(state="disabled")

    def _clear_log(self):
        self._diag_log.configure(state="normal")
        self._diag_log.delete("1.0", "end")
        self._diag_log.configure(state="disabled")

    def _run_connection_test(self):
        if self._blocked_by_test():
            return

        audit(self.actor, "DIAG_CONNECTION_TEST", "")

        self._log("── Test połączenia ──────────────────────")
        threading.Thread(target=self._do_connection_test, daemon=True).start()

    def _do_connection_test(self):
        serial_cfg = load_config().get("serial", {})
        port = serial_cfg.get("port", "COM11")
        baud = serial_cfg.get("baudrate", 9600)

        self.after(0, self._log, f"Port: {port} | Baudrate: {baud}")

        ctrl = HipotController(port=port, baudrate=baud, timeout=3)

        try:
            if runtime_state.test_in_progress():
                self.after(0, self._log, "⛔ Test wystartował — przerywam.")
                return

            ctrl.connect()
            self.after(0, self._log, "✔ Połączono z portem")

            resp = ctrl._send("RESET", wait=0.4)
            ack = "✔ ACK" if b"\x06" in resp else "✘ NAK / brak odpowiedzi"
            self.after(0, self._log, f"SEND >> RESET | {ack} | raw: {resp!r}")

            status = ctrl._query("SA?", wait=0.5)
            self.after(0, self._log, f"QUERY >> SA? | RESP << {status!r}")
            self.after(0, self._log,
                       "   ↑ tę odpowiedź wpisz do config.json → "
                       "hipot.status_idle_tokens")

        except Exception as e:
            log.exception("Błąd testu połączenia")
            self.after(0, self._log, f"❌ Błąd: {e}")

        finally:
            ctrl.disconnect()
            self.after(0, self._log, "── Koniec testu ─────────────────────────")

    def _send_manual_command(self):
        cmd = self._manual_cmd_entry.get().strip()

        if not cmd:
            return

        if self._blocked_by_test():
            return

        audit(self.actor, "DIAG_MANUAL_CMD", f"cmd={cmd}")

        threading.Thread(
            target=self._do_manual_command, args=(cmd,), daemon=True
        ).start()

    def _do_manual_command(self, cmd):
        serial_cfg = load_config().get("serial", {})

        ctrl = HipotController(
            port=serial_cfg.get("port", "COM11"),
            baudrate=serial_cfg.get("baudrate", 9600),
            timeout=3,
        )

        try:
            if runtime_state.test_in_progress():
                self.after(0, self._log, "⛔ Test wystartował — przerywam.")
                return

            ctrl.connect()

            if cmd.endswith("?"):
                resp = ctrl._query(cmd, wait=0.5)
                self.after(0, self._log, f"QUERY >> {cmd!r:20} | RESP << {resp!r}")
            else:
                resp = ctrl._send(cmd, wait=0.5)
                ack = "ACK ✔" if b"\x06" in resp else "NAK ✘"
                self.after(0, self._log,
                           f"SEND  >> {cmd!r:20} | {ack} | raw: {resp!r}")

        except Exception as e:
            log.exception("Błąd komendy ręcznej")
            self.after(0, self._log, f"❌ {e}")

        finally:
            ctrl.disconnect()

    # ══════════════════════════════════════════════════════════════════════
    # ZAKŁADKA: Bezpieczeństwo
    # ══════════════════════════════════════════════════════════════════════
    def _build_security_tab(self):
        tab = self.tabs.tab("Bezpieczeństwo")
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(7, weight=1)

        config = load_config()
        must_change = config.get("security", {}).get("must_change_password", False)

        if must_change:
            banner = ctk.CTkFrame(tab, fg_color=COLORS["fail"], corner_radius=8)
            banner.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(8, 12))

            ctk.CTkLabel(
                banner,
                text="⚠  Hasło inżynieryjne nie zostało jeszcze zmienione po "
                     "migracji. Ustaw własne hasło poniżej.",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLORS["text"],
                wraplength=740, justify="left",
            ).pack(padx=12, pady=10, anchor="w")

        ctk.CTkLabel(
            tab, text="Zmiana hasła inżynieryjnego",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 4))

        ctk.CTkLabel(
            tab,
            text="Hasło jest przechowywane wyłącznie jako hash PBKDF2 w config.json. "
                 "Nie ma go w kodzie ani w EXE.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
            wraplength=740, justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 12))

        self._pwd_entries = {}

        for i, (label, key) in enumerate([
            ("Obecne hasło:", "current"),
            ("Nowe hasło:", "new"),
            ("Powtórz nowe:", "repeat"),
        ]):
            ctk.CTkLabel(
                tab, text=label,
                font=ctk.CTkFont(size=12),
                text_color=COLORS["muted"], anchor="w", width=160,
            ).grid(row=3 + i, column=0, sticky="w", padx=(0, 12), pady=5)

            entry = ctk.CTkEntry(
                tab, font=ctk.CTkFont(size=13),
                height=34, width=240, corner_radius=8,
                border_color=COLORS["border"],
                show="•",
            )
            entry.grid(row=3 + i, column=1, sticky="w", pady=5)
            self._pwd_entries[key] = entry

        self._pwd_status = ctk.CTkLabel(
            tab, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["success"],
            wraplength=600, justify="left",
        )
        self._pwd_status.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 4))

        ctk.CTkButton(
            tab, text="🔐 Zmień hasło",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36, width=160, corner_radius=8,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._change_password,
        ).grid(row=6, column=1, sticky="e", pady=(8, 4))

        audit_frame = ctk.CTkFrame(tab, fg_color="transparent")
        audit_frame.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        audit_frame.grid_columnconfigure(0, weight=1)
        audit_frame.grid_rowconfigure(1, weight=1)

        header_row = ctk.CTkFrame(audit_frame, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_row, text="Log audytowy (logs/config_audit.log):",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            header_row, text="↺ Odśwież",
            font=ctk.CTkFont(size=12),
            height=28, width=100, corner_radius=8,
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._refresh_audit,
        ).grid(row=0, column=1)

        self._audit_box = ctk.CTkTextbox(
            audit_frame,
            font=ctk.CTkFont(family="Courier", size=11),
            fg_color=COLORS["surface"],
            text_color=COLORS["text"],
            corner_radius=8,
            height=200,
        )
        self._audit_box.grid(row=1, column=0, sticky="nsew")

        self._refresh_audit()

    def _refresh_audit(self):
        self._audit_box.configure(state="normal")
        self._audit_box.delete("1.0", "end")
        self._audit_box.insert("1.0", read_audit_tail(300))
        self._audit_box.see("end")
        self._audit_box.configure(state="disabled")

    def _change_password(self):
        current = self._pwd_entries["current"].get()
        new = self._pwd_entries["new"].get()
        repeat = self._pwd_entries["repeat"].get()

        config = load_config()

        if not verify_password(current, config):
            self._show_pwd_status("⚠ Obecne hasło jest nieprawidłowe", COLORS["fail"])
            audit(self.actor, "PASSWORD_CHANGE_FAILED", "błędne obecne hasło")
            return

        if len(new) < 10:
            self._show_pwd_status(
                "⚠ Nowe hasło musi mieć co najmniej 10 znaków", COLORS["fail"]
            )
            return

        if new != repeat:
            self._show_pwd_status("⚠ Nowe hasła nie są identyczne", COLORS["fail"])
            return

        if new == current:
            self._show_pwd_status("⚠ Nowe hasło musi być inne niż obecne",
                                  COLORS["fail"])
            return

        if not set_password(new, config):
            self._show_pwd_status("❌ Nie udało się zapisać konfiguracji",
                                  COLORS["fail"])
            return

        # Do audytu trafia wyłącznie fakt zmiany — nigdy samo hasło.
        audit(self.actor, "PASSWORD_CHANGE", "hasło inżynieryjne zmienione")
        log.info("Hasło inżynieryjne zmienione przez %s", self.actor)

        for entry in self._pwd_entries.values():
            entry.delete(0, "end")

        self._show_pwd_status("✔ Hasło zmienione", COLORS["success"])
        self._refresh_audit()

    def _show_pwd_status(self, msg, color):
        self._pwd_status.configure(text=msg, text_color=color)
        self.after(6000, lambda: self._pwd_status.configure(text=""))
