import threading
import customtkinter as ctk
from config import COLORS, load_config, resolve_profile_for_sn
from hipot_controller import HipotController


class MainScreen(ctk.CTkFrame):
    def __init__(self, parent, hrid: str, user: dict, on_logout):
        super().__init__(parent, fg_color=COLORS["bg"])
        self.hrid = hrid
        self.user = user
        self.on_logout = on_logout
        self._running = False
        self._active_profile = None
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
        self.sn_entry.bind("<Return>", lambda e: self._start_test())
        self.sn_entry.bind("<KeyRelease>", lambda e: self._on_sn_change())

        self.profile_label = ctk.CTkLabel(
            body,
            text="Profil: —",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w"
        )
        self.profile_label.grid(row=2, column=0, sticky="w", pady=(0, 20))

        self.result_label = ctk.CTkLabel(
            body, text="—",
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color=COLORS["muted"]
        )
        self.result_label.grid(row=3, column=0, pady=(0, 8))

        details = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        details.grid(row=4, column=0, sticky="ew", pady=(0, 20))
        details.grid_columnconfigure((0, 1, 2), weight=1)

        self.volt_lbl = self._detail_cell(details, "Napięcie", "—", 0)
        self.curr_lbl = self._detail_cell(details, "Prąd", "—", 1)
        self.time_lbl = self._detail_cell(details, "Czas", "—", 2)

        self.test_btn = ctk.CTkButton(
            body, text="▶ START TEST",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52, corner_radius=10,
            fg_color=COLORS["primary"],
            hover_color="#005a9e",
            command=self._start_test
        )
        self.test_btn.grid(row=5, column=0, sticky="ew")

        self.status_lbl = ctk.CTkLabel(
            body, text="Gotowy",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"]
        )
        self.status_lbl.grid(row=6, column=0, pady=(8, 0))

    def _detail_cell(self, parent, label, value, col):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=col, padx=8, pady=12, sticky="ew")
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

    def _set_status(self, msg, color=None):
        self.status_lbl.configure(text=msg, text_color=color or COLORS["muted"])

    def _reset_display(self):
        self.result_label.configure(text="—", text_color=COLORS["muted"])
        self.volt_lbl.configure(text="—")
        self.curr_lbl.configure(text="—")
        self.time_lbl.configure(text="—")

    def _on_sn_change(self):
        sn = self.sn_entry.get().strip()
        if len(sn) < 6:
            self.profile_label.configure(
                text="Profil: —",
                text_color=COLORS["muted"]
            )
            self._active_profile = None
            self._active_profile_key = None
            return

        key, profile = resolve_profile_for_sn(sn)

        if profile:
            self._active_profile_key = key
            self._active_profile = profile
            name = profile.get("name", key)
            v    = profile.get("voltage")
            hi   = profile.get("hi_limit")
            lo   = profile.get("lo_limit")
            dw   = profile.get("dwell")
            self.profile_label.configure(
                text=f"✔ {name}  |  {v} kV  |  {lo}–{hi} mA  |  dwell {dw}s",
                text_color=COLORS["primary"]
            )
        else:
            self._active_profile = None
            self._active_profile_key = None
            self.profile_label.configure(
                text="❌ Nieznany SN — brak profilu",
                text_color=COLORS["fail"]
            )

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

    def _run_thread(self, sn):
        config = load_config()
        serial_cfg = config.get("serial", {})
        ctrl = HipotController(
            port=serial_cfg.get("port", "COM11"),
            baudrate=serial_cfg.get("baudrate", 9600),
            timeout=serial_cfg.get("timeout", 3)
        )
        try:
            ctrl.connect()
            self.after(0, self._set_status, "Test uruchomiony...", COLORS["primary"])
            result = ctrl.program_and_run(self._active_profile)
            self.after(0, self._show_result, sn, result)
        except Exception as e:
            self.after(0, self._set_status, f"❌ {e}", COLORS["fail"])
            self.after(0, self.result_label.configure,
                       {"text": "ERROR", "text_color": COLORS["fail"]})
        finally:
            ctrl.disconnect()
            self._running = False
            self.after(0, self.test_btn.configure,
                       {"state": "normal", "text": "▶ START TEST"})

    def _show_result(self, sn, r):
        error  = r.get("error")
        result = r.get("result", "")
        if error:
            self.result_label.configure(text="ERROR", text_color=COLORS["fail"])
            self._set_status(f"❌ {error}", COLORS["fail"])
            return
        if result == "Pass":
            self.result_label.configure(text="✔ PASS", text_color=COLORS["success"])
            self._set_status(f"✔ PASS | SN: {sn}", COLORS["success"])
        elif result == "Fail":
            self.result_label.configure(text="✘ FAIL", text_color=COLORS["fail"])
            self._set_status(f"✘ FAIL | SN: {sn}", COLORS["fail"])
        else:
            status = r.get("status", "").upper()
            self.result_label.configure(text=f"⚠ {status}", text_color=COLORS["warning"])
            self._set_status(f"⚠ {status} | SN: {sn}", COLORS["warning"])
        self.volt_lbl.configure(text=f"{r.get('voltage', '—')} kV")
        self.curr_lbl.configure(text=f"{r.get('current', '—')} mA")
        self.time_lbl.configure(text=f"{r.get('time', '—')} s")