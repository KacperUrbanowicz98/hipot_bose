import threading
from datetime import datetime, timezone

import customtkinter as ctk

from config import COLORS, load_config, resolve_profile_for_sn
from hipot_controller import HipotController
from result_logger import save_result
from ted_client import send_to_ted

# ── Stałe TED (nadpisywalne przez config) ─────────────────────────────────
TED_CONTRACT     = "10058"
TED_PROGRAM      = "BOSE_BYD"
TED_MACHINE_NAME = "HIPOT"

# Opóźnienie bannera GND [ms] — widoczny przez tyle zanim zniknie
GND_BANNER_MS = 3000


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

    # ── UI ─────────────────────────────────────────────────────────────────
    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Nagłówek
        header = ctk.CTkFrame(self, fg_color=COLORS["surface"],
                               corner_radius=0, height=52)
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

        # Body
        body = ctk.CTkFrame(self, fg_color=COLORS["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=32, pady=24)
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body, text="Numer seryjny (SN)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"], anchor="w"
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.sn_entry = ctk.CTkEntry(
            body, placeholder_text="Zeskanuj lub wpisz SN...",
            font=ctk.CTkFont(size=14), height=42, corner_radius=8,
            border_color=COLORS["border"]
        )
        self.sn_entry.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.sn_entry.bind("<Return>",     lambda e: self._start_test())
        self.sn_entry.bind("<KeyRelease>", lambda e: self._on_sn_change())

        self.profile_label = ctk.CTkLabel(
            body, text="Profil: —",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w"
        )
        self.profile_label.grid(row=2, column=0, sticky="w", pady=(0, 12))

        # Banner GND (domyślnie ukryty)
        self.gnd_banner = ctk.CTkLabel(
            body,
            text="🔄 Przełączanie na Ground Bond...",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#ffffff",
            fg_color=COLORS.get("warning", "#b45309"),
            corner_radius=8, height=36
        )
        # NIE dodajemy do gridu — pojawia się dynamicznie

        self.result_label = ctk.CTkLabel(
            body, text="—",
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color=COLORS["muted"]
        )
        self.result_label.grid(row=4, column=0, pady=(0, 8))

        # Sekcja GND Bond
        gnd_frame = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        gnd_frame.grid(row=5, column=0, sticky="ew", pady=(0, 6))
        gnd_frame.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(
            gnd_frame, text="Ground Bond",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 0))
        self.gnd_res_lbl  = self._detail_cell(gnd_frame, "Rezystancja", "—", 0, row=1)
        self.gnd_cur_lbl  = self._detail_cell(gnd_frame, "Prąd",        "—", 1, row=1)
        self.gnd_time_lbl = self._detail_cell(gnd_frame, "Czas",        "—", 2, row=1)

        # Sekcja Hipot
        hipot_frame = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        hipot_frame.grid(row=6, column=0, sticky="ew", pady=(0, 16))
        hipot_frame.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(
            hipot_frame, text="Hipot",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"]
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 0))
        self.volt_lbl = self._detail_cell(hipot_frame, "Napięcie", "—", 0, row=1)
        self.curr_lbl = self._detail_cell(hipot_frame, "Prąd",     "—", 1, row=1)
        self.time_lbl = self._detail_cell(hipot_frame, "Czas",     "—", 2, row=1)

        self.test_btn = ctk.CTkButton(
            body, text="▶ START TEST",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52, corner_radius=10,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._start_test
        )
        self.test_btn.grid(row=7, column=0, sticky="ew")

        self.status_lbl = ctk.CTkLabel(
            body, text="Gotowy",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"]
        )
        self.status_lbl.grid(row=8, column=0, pady=(8, 0))

    def _detail_cell(self, parent, label, value, col, row=0):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=row, column=col, padx=8, pady=(4, 12), sticky="ew")
        ctk.CTkLabel(
            f, text=label,
            font=ctk.CTkFont(size=11), text_color=COLORS["muted"]
        ).pack()
        val = ctk.CTkLabel(
            f, text=value,
            font=ctk.CTkFont(size=15, weight="bold"), text_color=COLORS["text"]
        )
        val.pack()
        return val

    # ── Helpers UI ─────────────────────────────────────────────────────────
    def _set_status(self, msg, color=None):
        self.status_lbl.configure(text=msg, text_color=color or COLORS["muted"])

    def _reset_display(self):
        self.result_label.configure(text="—", text_color=COLORS["muted"])
        for lbl in (self.volt_lbl, self.curr_lbl, self.time_lbl,
                    self.gnd_res_lbl, self.gnd_cur_lbl, self.gnd_time_lbl):
            lbl.configure(text="—")

    def _show_gnd_banner(self):
        """Pokazuje pomarańczowy banner na 3 sekundy, potem sam znika."""
        self.gnd_banner.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.after(GND_BANNER_MS, self._hide_gnd_banner)

    def _hide_gnd_banner(self):
        self.gnd_banner.grid_forget()

    # ── Logika SN ─────────────────────────────────────────────────────────
    def _on_sn_change(self):
        sn = self.sn_entry.get().strip()
        if len(sn) < 6:
            self.profile_label.configure(text="Profil: —", text_color=COLORS["muted"])
            self._active_profile = None
            self._active_profile_key = None
            return

        key, profile = resolve_profile_for_sn(sn)
        if profile:
            self._active_profile_key = key
            self._active_profile = profile
            name    = profile.get("name", key)
            v       = profile.get("voltage")
            hi      = profile.get("hi_limit")
            lo      = profile.get("lo_limit")
            dw      = profile.get("dwell")
            gnd_cur = profile.get("gnd_current")
            gnd_txt = f"  |  GND {gnd_cur}A" if gnd_cur else ""
            self.profile_label.configure(
                text=f"✔ {name}  |  {v} kV  |  {lo}–{hi} mA  |  dwell {dw}s{gnd_txt}",
                text_color=COLORS["primary"]
            )
        else:
            self._active_profile = None
            self._active_profile_key = None
            self.profile_label.configure(
                text="❌ Nieznany SN — brak profilu",
                text_color=COLORS["fail"]
            )

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
    def _run_thread(self, sn: str):
        config     = load_config()
        serial_cfg = config.get("serial", {})
        ted_cfg    = config.get("ted", {})
        log_dir    = config.get("log_dir", "./logs")
        db_type    = ted_cfg.get("db_type", "TEST")   # "" = prod, "TEST" = test

        ctrl = HipotController(
            port=serial_cfg.get("port", "COM11"),
            baudrate=serial_cfg.get("baudrate", 9600),
            timeout=serial_cfg.get("timeout", 3),
        )

        hipot_result = {}
        gnd_result   = None
        start_time   = datetime.now(timezone.utc)

        try:
            ctrl.connect()
            profile = self._active_profile

            # ── 1. Hipot ───────────────────────────────────────────────────
            self.after(0, self._set_status, "⚙ Hipot...", COLORS["primary"])
            hipot_result = ctrl.program_and_run(profile)
            self.after(0, self._show_hipot_result, hipot_result)

            # ── 2. GND Bond (opcjonalny) ───────────────────────────────────
            gnd_profile = self._build_gnd_profile(profile)
            if gnd_profile:
                # Banner informacyjny — znika po GND_BANNER_MS automatycznie
                self.after(0, self._show_gnd_banner)
                self.after(0, self._set_status,
                           "🔄 Przełączanie na Ground Bond...", COLORS.get("warning", "#b45309"))

                # Czekaj tyle samo co banner żeby operator zdążył zobaczyć
                import time
                time.sleep(GND_BANNER_MS / 1000)

                self.after(0, self._set_status, "⚙ Ground Bond...", COLORS["primary"])
                gnd_result = ctrl.run_ground_bond(gnd_profile)
                self.after(0, self._show_gnd_result, gnd_result)

            end_time = datetime.now(timezone.utc)

            # ── 3. Wynik globalny ──────────────────────────────────────────
            self.after(0, self._show_global_result, sn, hipot_result, gnd_result)

            # ── 4. Zapis CSV ───────────────────────────────────────────────
            ted_status = {"ok": False, "error": "nie wysłano jeszcze"}
            csv_path = save_result(
                log_dir=log_dir,
                sn=sn,
                operator=self.hrid,
                profile_name=profile.get("name", self._active_profile_key or ""),
                hipot=hipot_result,
                gnd=gnd_result,
                ted_status=ted_status,  # placeholder, zaktualizujemy poniżej
            )

            # ── 5. Wysyłka do TED ──────────────────────────────────────────
            self.after(0, self._set_status, "📡 Wysyłanie do TED...", COLORS["muted"])
            ted_payload = self._build_ted_payload(
                sn=sn,
                profile=profile,
                hipot=hipot_result,
                gnd=gnd_result,
                start_time=start_time,
                end_time=end_time,
                csv_path=csv_path,
                ted_cfg=ted_cfg,
            )
            ted_status = send_to_ted(ted_payload, db_type=db_type)

            # Zaktualizuj CSV z faktycznym statusem TED
            save_result(
                log_dir=log_dir,
                sn=sn,
                operator=self.hrid,
                profile_name=profile.get("name", self._active_profile_key or ""),
                hipot=hipot_result,
                gnd=gnd_result,
                ted_status=ted_status,
            )

            if ted_status.get("ok"):
                self.after(0, self._set_status,
                           f"✔ TED OK | SN: {sn}", COLORS["success"])
            else:
                self.after(0, self._set_status,
                           f"⚠ TED błąd: {ted_status.get('error','')} | SN: {sn}",
                           COLORS.get("warning", "#b45309"))

        except Exception as e:
            self.after(0, self._set_status, f"❌ {e}", COLORS["fail"])
            self.after(0, self.result_label.configure,
                       {"text": "ERROR", "text_color": COLORS["fail"]})
        finally:
            ctrl.disconnect()
            self._running = False
            self.after(0, self.test_btn.configure,
                       {"state": "normal", "text": "▶ START TEST"})

    # ── Budowanie payloadów ────────────────────────────────────────────────
    def _build_gnd_profile(self, profile: dict) -> dict | None:
        if not profile.get("gnd_current"):
            return None
        return {
            "current":   profile.get("gnd_current",  10.0),
            "hi_limit":  profile.get("gnd_hi_limit", 100),
            "lo_limit":  profile.get("gnd_lo_limit", 0),
            "dwell":     profile.get("gnd_dwell",    1.0),
            "offset":    profile.get("gnd_offset",   0),
            "frequency": profile.get("gnd_frequency", 1),
        }

    def _build_ted_payload(
        self, sn, profile, hipot, gnd,
        start_time, end_time, csv_path, ted_cfg
    ) -> dict:
        hipot_ok = hipot.get("result") == "Pass"
        gnd_ok   = (gnd is None) or (gnd.get("result") == "Pass")
        result   = "PASS" if (hipot_ok and gnd_ok) else "FAIL"

        fail_num = "0000"
        err_desc = ""
        if not hipot_ok:
            fail_num = hipot.get("error_code", "FAIL")
            err_desc = hipot.get("error_desc", "Hipot FAIL")
        elif gnd and not gnd_ok:
            fail_num = gnd.get("error_code", "GND_FAIL")
            err_desc = gnd.get("error_desc", "GND Bond FAIL")

        misc = "HIPOT+GND" if gnd else "HIPOT"

        # Subtesty
        subtests = [
            {
                "id":             "1",
                "name":           "HIPOT",
                "desc":           f"AC {profile.get('voltage')} kV  {profile.get('lo_limit')}–{profile.get('hi_limit')} mA  dwell {profile.get('dwell')}s",
                "start_time":     start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_time":       end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "result":         "PASS" if hipot_ok else "FAIL",
                "error_message":  err_desc if not hipot_ok else "",
                "result_message": (
                    f"V={hipot.get('voltage','')} kV  "
                    f"I={hipot.get('current','')} mA  "
                    f"T={hipot.get('time','')} s"
                ),
            }
        ]
        if gnd:
            subtests.append({
                "id":             "2",
                "name":           "GROUND_BOND",
                "desc":           f"GND {profile.get('gnd_current')}A  max {profile.get('gnd_hi_limit')} mΩ  dwell {profile.get('gnd_dwell')}s",
                "start_time":     start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_time":       end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "result":         "PASS" if gnd_ok else "FAIL",
                "error_message":  gnd.get("error_desc", "") if not gnd_ok else "",
                "result_message": (
                    f"R={gnd.get('resistance','')}  "
                    f"I={gnd.get('current','')}  "
                    f"T={gnd.get('time','')} s"
                ),
            })

        return {
            "serial_number":  sn,
            "contract":       ted_cfg.get("contract",     TED_CONTRACT),
            "program":        ted_cfg.get("program",      TED_PROGRAM),
            "machine_name":   ted_cfg.get("machine_name", TED_MACHINE_NAME),
            "test_area":      ted_cfg.get("test_area",    ""),
            "cell_number":    ted_cfg.get("cell_number",  ""),
            "username":       self.hrid,
            "start_time":     start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time":       end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "result":         result,
            "misc_info":      misc,
            "msg":            profile.get("name", ""),
            "failure_number": fail_num,
            "error_desc":     err_desc,
            "csv_path":       csv_path,
            "subtests":       subtests,
        }

    # ── Wyświetlanie wyników ───────────────────────────────────────────────
    def _show_hipot_result(self, r):
        if r.get("error"):
            self.volt_lbl.configure(text="ERR")
            return
        self.volt_lbl.configure(text=f"{r.get('voltage', '—')} kV")
        self.curr_lbl.configure(text=f"{r.get('current', '—')} mA")
        self.time_lbl.configure(text=f"{r.get('time',    '—')} s")

    def _show_gnd_result(self, r):
        if r.get("error"):
            self.gnd_res_lbl.configure(text="ERR")
            return
        self.gnd_res_lbl.configure(text=r.get("resistance", "—"))
        self.gnd_cur_lbl.configure(text=r.get("current",    "—"))
        self.gnd_time_lbl.configure(text=r.get("time",      "—"))

    def _show_global_result(self, sn, hipot, gnd):
        hipot_ok = hipot.get("result") == "Pass"
        gnd_ok   = (gnd is None) or (gnd.get("result") == "Pass")

        if hipot.get("error"):
            self.result_label.configure(text="ERROR", text_color=COLORS["fail"])
            self._set_status(f"❌ {hipot['error']}", COLORS["fail"])
            return

        if hipot_ok and gnd_ok:
            self.result_label.configure(text="✔ PASS", text_color=COLORS["success"])
        else:
            self.result_label.configure(text="✘ FAIL", text_color=COLORS["fail"])
            desc = hipot.get("error_desc") or (gnd.get("error_desc") if gnd else "")
            self._set_status(
                f"✘ FAIL | {desc} | SN: {sn}" if desc else f"✘ FAIL | SN: {sn}",
                COLORS["fail"]
            )