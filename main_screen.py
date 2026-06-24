import threading
import customtkinter as ctk
from config import COLORS, load_config, resolve_profile_for_sn
from hipot_controller import HipotController


class MainScreen(ctk.CTkFrame):
    def __init__(self, parent, hrid: str, user: dict, on_logout):
        super().__init__(parent, fg_color=COLORS["bg"])
        self.hrid     = hrid
        self.user     = user
        self.on_logout = on_logout
        self._running  = False
        self._active_profile     = None
        self._active_profile_key = None
        self._build()

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Nagłówek ───────────────────────────────────────────────────────
        header = ctk.CTkFrame(
            self, fg_color=COLORS["surface"],
            corner_radius=0, height=52
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="⚡ HiPot Tester",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"]
        ).grid(row=0, column=0, padx=20, pady=14)

        ctk.CTkLabel(
            header,
            text=f"👤 {self.user['name']} | {self.hrid} | {self.user['role'].upper()}",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"]
        ).grid(row=0, column=1, padx=20)

        ctk.CTkButton(
            header, text="Wyloguj", width=90, height=30,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self.on_logout
        ).grid(row=0, column=2, padx=20)

        # ── Body ───────────────────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color=COLORS["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=32, pady=24)
        body.grid_columnconfigure(0, weight=1)

        # SN input
        ctk.CTkLabel(
            body, text="Numer seryjny (SN)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"], anchor="w"
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.sn_entry = ctk.CTkEntry(
            body,
            placeholder_text="Zeskanuj lub wpisz SN...",
            font=ctk.CTkFont(size=14),
            height=42, corner_radius=8,
            border_color=COLORS["border"]
        )
        self.sn_entry.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.sn_entry.bind("<Return>",   lambda e: self._start_test())
        self.sn_entry.bind("<KeyRelease>", lambda e: self._on_sn_change())

        self.profile_label = ctk.CTkLabel(
            body,
            text="Profil: —",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w"
        )
        self.profile_label.grid(row=2, column=0, sticky="w", pady=(0, 16))

        # ── Główny wynik (duży) ────────────────────────────────────────────
        self.result_label = ctk.CTkLabel(
            body, text="—",
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color=COLORS["muted"]
        )
        self.result_label.grid(row=3, column=0, pady=(0, 4))

        # ── Krok aktywny (co teraz trwa) ───────────────────────────────────
        self.step_label = ctk.CTkLabel(
            body, text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"]
        )
        self.step_label.grid(row=4, column=0, pady=(0, 12))

        # ── Karta HiPot ────────────────────────────────────────────────────
        hipot_card = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        hipot_card.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        hipot_card.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(
            hipot_card, text="HiPot AC",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        self.hipot_result_lbl = self._detail_cell(hipot_card, "Wynik",    "—", 0, row=1)
        self.volt_lbl         = self._detail_cell(hipot_card, "Napięcie", "—", 1, row=1)
        self.curr_lbl         = self._detail_cell(hipot_card, "Prąd",     "—", 2, row=1)
        self.time_lbl         = self._detail_cell(hipot_card, "Czas",     "—", 3, row=1)

        # ── Karta Ground Bond (ukryta gdy profil nie ma GND) ───────────────
        self.gnd_card = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        self.gnd_card.grid(row=6, column=0, sticky="ew", pady=(0, 16))
        self.gnd_card.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(
            self.gnd_card, text="Ground Bond",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        self.gnd_result_lbl = self._detail_cell(self.gnd_card, "Wynik",       "—", 0, row=1)
        self.gnd_res_lbl    = self._detail_cell(self.gnd_card, "Rezystancja", "—", 1, row=1)
        self.gnd_curr_lbl   = self._detail_cell(self.gnd_card, "Prąd",        "—", 2, row=1)
        self.gnd_time_lbl   = self._detail_cell(self.gnd_card, "Czas",        "—", 3, row=1)

        # domyślnie ukryj kartę GND — pokaże się gdy profil ma ground_bond
        self.gnd_card.grid_remove()

        # ── START button ───────────────────────────────────────────────────
        self.test_btn = ctk.CTkButton(
            body, text="▶ START TEST",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52, corner_radius=10,
            fg_color=COLORS["primary"],
            hover_color="#005a9e",
            command=self._start_test
        )
        self.test_btn.grid(row=7, column=0, sticky="ew")

        self.status_lbl = ctk.CTkLabel(
            body, text="Gotowy",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"]
        )
        self.status_lbl.grid(row=8, column=0, pady=(8, 0))

    # ── Helper: komórka szczegółów ─────────────────────────────────────────
    def _detail_cell(self, parent, label, value, col, row=0):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=row, column=col, padx=8, pady=12, sticky="ew")
        ctk.CTkLabel(
            f, text=label,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"]
        ).pack()
        val = ctk.CTkLabel(
            f, text=value,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"]
        )
        val.pack()
        return val

    # ── Helpers UI ─────────────────────────────────────────────────────────
    def _set_status(self, msg, color=None):
        self.status_lbl.configure(text=msg, text_color=color or COLORS["muted"])

    def _set_step(self, msg, color=None):
        self.step_label.configure(text=msg, text_color=color or COLORS["muted"])

    def _reset_display(self):
        self.result_label.configure(text="—", text_color=COLORS["muted"])
        self.step_label.configure(text="")
        # HiPot
        self.hipot_result_lbl.configure(text="—", text_color=COLORS["text"])
        self.volt_lbl.configure(text="—")
        self.curr_lbl.configure(text="—")
        self.time_lbl.configure(text="—")
        # GND
        self.gnd_result_lbl.configure(text="—", text_color=COLORS["text"])
        self.gnd_res_lbl.configure(text="—")
        self.gnd_curr_lbl.configure(text="—")
        self.gnd_time_lbl.configure(text="—")

    # ── Zmiana SN → wykryj profil ──────────────────────────────────────────
    def _on_sn_change(self):
        sn = self.sn_entry.get().strip()
        if len(sn) < 6:
            self.profile_label.configure(text="Profil: —", text_color=COLORS["muted"])
            self._active_profile     = None
            self._active_profile_key = None
            self.gnd_card.grid_remove()
            return

        key, profile = resolve_profile_for_sn(sn)

        if profile:
            self._active_profile_key = key
            self._active_profile     = profile
            has_gnd = profile.get("ground_bond") is not None
            name    = profile.get("name", key)
            v       = profile.get("voltage")
            hi      = profile.get("hi_limit")
            lo      = profile.get("lo_limit")
            dw      = profile.get("dwell")
            gnd_tag = " | + Ground Bond" if has_gnd else ""
            self.profile_label.configure(
                text=f"✔ {name} | {v} kV | {lo}–{hi} mA | dwell {dw}s{gnd_tag}",
                text_color=COLORS["primary"]
            )
            # pokaż/ukryj kartę GND zależnie od profilu
            if has_gnd:
                self.gnd_card.grid()
            else:
                self.gnd_card.grid_remove()
        else:
            self._active_profile     = None
            self._active_profile_key = None
            self.profile_label.configure(
                text="❌ Nieznany SN — brak profilu",
                text_color=COLORS["fail"]
            )
            self.gnd_card.grid_remove()

    # ── Start testu ────────────────────────────────────────────────────────
    def _start_test(self):
        sn = self.sn_entry.get().strip()
        if not sn:
            self._set_status("⚠ Wprowadź numer seryjny.", COLORS["fail"])
            return
        if not self._active_profile:
            self._set_status("⚠ Brak profilu dla tego SN.", COLORS["fail"])
            return
        if self._running:
            return
        self._running = True
        self.test_btn.configure(state="disabled", text="⏳ Test w toku...")
        self._reset_display()
        self._set_status("Łączenie z HiPotem...", COLORS["primary"])
        threading.Thread(target=self._run_thread, args=(sn,), daemon=True).start()

    # ── Wątek testowy ──────────────────────────────────────────────────────
    def _run_thread(self, sn):
        config     = load_config()
        serial_cfg = config.get("serial", {})
        ctrl       = HipotController(
            port=serial_cfg.get("port",     "COM11"),
            baudrate=serial_cfg.get("baudrate", 9600),
            timeout=serial_cfg.get("timeout",  3),
            relay_port=serial_cfg.get("relay_port")
        )
        try:
            ctrl.connect()

            has_gnd = self._active_profile.get("ground_bond") is not None

            # ── Krok 1: HiPot ──────────────────────────────────────────────
            self.after(0, self._set_step,   "🔌 Krok 1/2: HiPot AC..." if has_gnd
                                        else "🔌 HiPot AC...", COLORS["primary"])
            self.after(0, self._set_status, "Test HiPot uruchomiony...", COLORS["primary"])

            seq = ctrl.run_full_sequence(self._active_profile)

            self.after(0, self._show_result, sn, seq)

        except Exception as e:
            self.after(0, self._set_status, f"❌ {e}", COLORS["fail"])
            self.after(0, self.result_label.configure,
                       {"text": "ERROR", "text_color": COLORS["fail"]})
        finally:
            ctrl.disconnect()
            self._running = False
            self.after(0, self.test_btn.configure,
                       {"state": "normal", "text": "▶ START TEST"})
            self.after(0, self._set_step, "")

    # ── Wyświetl wynik sekwencji ────────────────────────────────────────────
    def _show_result(self, sn, seq: dict):
        """
        seq = {"hipot": {...}, "gnd": {...} | None}
        Końcowy PASS tylko gdy wszystkie wykonane testy przeszły.
        """
        hipot = seq.get("hipot", {})
        gnd   = seq.get("gnd")

        # ── Uzupełnij kartę HiPot ──────────────────────────────────────────
        hipot_error  = hipot.get("error")
        hipot_result = hipot.get("result", "")
        hipot_status = hipot.get("status", "")

        if hipot_error:
            self.hipot_result_lbl.configure(text="ERR", text_color=COLORS["fail"])
            self._show_final("ERROR", COLORS["fail"], f"❌ HiPot błąd: {hipot_error}")
            return

        if hipot_result == "Pass":
            self.hipot_result_lbl.configure(text="PASS", text_color=COLORS["success"])
        elif hipot_result == "Fail":
            self.hipot_result_lbl.configure(text="FAIL", text_color=COLORS["fail"])
        else:
            self.hipot_result_lbl.configure(
                text=hipot_status.upper() or "?", text_color=COLORS["warning"]
            )

        self.volt_lbl.configure(text=f"{hipot.get('voltage', '—')} kV")
        self.curr_lbl.configure(text=f"{hipot.get('current', '—')} mA")
        self.time_lbl.configure(text=f"{hipot.get('time',    '—')} s")

        # ── HiPot FAIL → koniec ────────────────────────────────────────────
        if hipot_result != "Pass":
            desc = hipot.get("error_desc", "")
            self._show_final(
                "✘ FAIL", COLORS["fail"],
                f"✘ FAIL HiPot | SN: {sn}" + (f" | {desc}" if desc else "")
            )
            return

        # ── Brak Ground Bond w profilu → PASS ─────────────────────────────
        if gnd is None:
            self._show_final("✔ PASS", COLORS["success"], f"✔ PASS | SN: {sn}")
            return

        # ── Uzupełnij kartę Ground Bond ────────────────────────────────────
        gnd_error  = gnd.get("error")
        gnd_result = gnd.get("result", "")
        gnd_status = gnd.get("status", "")

        if gnd_error:
            self.gnd_result_lbl.configure(text="ERR", text_color=COLORS["fail"])
            self._show_final("ERROR", COLORS["fail"], f"❌ Ground Bond błąd: {gnd_error}")
            return

        if gnd_result == "Pass":
            self.gnd_result_lbl.configure(text="PASS", text_color=COLORS["success"])
        elif gnd_result == "Fail":
            self.gnd_result_lbl.configure(text="FAIL", text_color=COLORS["fail"])
        else:
            self.gnd_result_lbl.configure(
                text=gnd_status.upper() or "?", text_color=COLORS["warning"]
            )

        self.gnd_res_lbl.configure( text=f"{gnd.get('resistance', '—')} mΩ")
        self.gnd_curr_lbl.configure(text=f"{gnd.get('current',    '—')} A")
        self.gnd_time_lbl.configure(text=f"{gnd.get('time',       '—')} s")

        # ── Końcowy werdykt ────────────────────────────────────────────────
        if gnd_result == "Pass":
            self._show_final("✔ PASS", COLORS["success"], f"✔ PASS | SN: {sn}")
        else:
            desc = gnd.get("error_desc", "")
            self._show_final(
                "✘ FAIL", COLORS["fail"],
                f"✘ FAIL Ground Bond | SN: {sn}" + (f" | {desc}" if desc else "")
            )

    def _show_final(self, text: str, color: str, status_msg: str):
        self.result_label.configure(text=text, text_color=color)
        self._set_status(status_msg, color)