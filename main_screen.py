import threading
from datetime import datetime, timezone

import customtkinter as ctk

from config import COLORS, load_config, resolve_profile_for_sn
from hipot_controller import HipotController
from ted_client import build_hipot_payload, send_to_ted
from result_logger import save_result as save_csv_result


class MainScreen(ctk.CTkFrame):
    def __init__(self, parent, hrid: str, user: dict, on_logout):
        super().__init__(parent, fg_color=COLORS["bg"])
        self.hrid = hrid
        self.user = user
        self.on_logout = on_logout

        self._running = False
        self._abort = threading.Event()

        self._active_profile = None
        self._active_profile_key = None

        self._build()

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Nagłówek ───────────────────────────────────────────────────────
        header = ctk.CTkFrame(
            self,
            fg_color=COLORS["surface"],
            corner_radius=0,
            height=52,
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="⚡ HiPot Tester",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, padx=20, pady=14)

        ctk.CTkLabel(
            header,
            text=f"👤 {self.user['name']} | {self.hrid} | {self.user['role'].upper()}",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=0, column=1, padx=20)

        ctk.CTkButton(
            header,
            text="Wyloguj",
            width=90,
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self.on_logout,
        ).grid(row=0, column=2, padx=20)

        # ── Body ───────────────────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color=COLORS["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=32, pady=24)
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body,
            text="Numer seryjny (SN)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.sn_entry = ctk.CTkEntry(
            body,
            placeholder_text="Zeskanuj lub wpisz SN...",
            font=ctk.CTkFont(size=14),
            height=42,
            corner_radius=8,
            border_color=COLORS["border"],
        )
        self.sn_entry.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.sn_entry.bind("<Return>", lambda e: self._start_test())
        self.sn_entry.bind("<KeyRelease>", lambda e: self._on_sn_change())

        self.profile_label = ctk.CTkLabel(
            body,
            text="Profil: —",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            anchor="w",
        )
        self.profile_label.grid(row=2, column=0, sticky="w", pady=(0, 20))

        # ── Wynik główny ───────────────────────────────────────────────────
        self.result_label = ctk.CTkLabel(
            body,
            text="—",
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color=COLORS["muted"],
        )
        self.result_label.grid(row=3, column=0, pady=(0, 8))

        # ── Szczegóły HiPot ────────────────────────────────────────────────
        details = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        details.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        details.grid_columnconfigure((0, 1, 2), weight=1)

        self.volt_lbl = self._detail_cell(details, "Napięcie", "—", 0)
        self.curr_lbl = self._detail_cell(details, "Prąd", "—", 1)
        self.time_lbl = self._detail_cell(details, "Czas", "—", 2)

        # ── Szczegóły Ground Bond ──────────────────────────────────────────
        self.gnd_frame = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        self.gnd_frame.grid(row=5, column=0, sticky="ew", pady=(0, 16))
        self.gnd_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.gnd_frame.grid_remove()

        ctk.CTkLabel(
            self.gnd_frame,
            text="Ground Bond",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, columnspan=3, pady=(8, 0))

        self.gnd_result_lbl = ctk.CTkLabel(
            self.gnd_frame,
            text="—",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["muted"],
        )
        self.gnd_result_lbl.grid(row=1, column=0, columnspan=3, pady=(2, 4))

        self.gnd_res_lbl = self._detail_cell(self.gnd_frame, "Rezystancja", "—", 0)
        self.gnd_curr_lbl = self._detail_cell(self.gnd_frame, "Prąd", "—", 1)
        self.gnd_time_lbl = self._detail_cell(self.gnd_frame, "Czas", "—", 2)

        # ── Przyciski START / ABORT ────────────────────────────────────────
        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.grid(row=6, column=0, sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)

        self.test_btn = ctk.CTkButton(
            btn_row,
            text="▶ START TEST",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52,
            corner_radius=10,
            fg_color=COLORS["primary"],
            hover_color="#005a9e",
            command=self._start_test,
        )
        self.test_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.abort_btn = ctk.CTkButton(
            btn_row,
            text="⏹ ABORT",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52,
            width=130,
            corner_radius=10,
            fg_color=COLORS["fail"],
            hover_color="#8b0000",
            state="disabled",
            command=self._abort_test,
        )
        self.abort_btn.grid(row=0, column=1)

        self.status_lbl = ctk.CTkLabel(
            body,
            text="Gotowy",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        self.status_lbl.grid(row=7, column=0, pady=(8, 0))

    def _detail_cell(self, parent, label, value, col):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=2, column=col, padx=8, pady=12, sticky="ew")

        ctk.CTkLabel(
            f,
            text=label,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        ).pack()

        val = ctk.CTkLabel(
            f,
            text=value,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"],
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

        self.gnd_frame.grid_remove()
        self.gnd_result_lbl.configure(text="—", text_color=COLORS["muted"])
        self.gnd_res_lbl.configure(text="—")
        self.gnd_curr_lbl.configure(text="—")
        self.gnd_time_lbl.configure(text="—")

    def _on_sn_change(self):
        sn = self.sn_entry.get().strip()

        if len(sn) < 6:
            self.profile_label.configure(text="Profil: —", text_color=COLORS["muted"])
            self._active_profile = None
            self._active_profile_key = None
            self.gnd_frame.grid_remove()
            return

        key, profile = resolve_profile_for_sn(sn)

        if profile:
            self._active_profile_key = key
            self._active_profile = profile

            name = profile.get("name", key)
            v = profile.get("voltage")
            hi = profile.get("hi_limit")
            lo = profile.get("lo_limit")
            dw = profile.get("dwell")

            has_gnd = profile.get("ground_bond") is not None
            gnd_tag = " | 🔗 GND" if has_gnd else ""

            self.profile_label.configure(
                text=f"✔ {name} | {v} kV | {lo}–{hi} mA | dwell {dw}s{gnd_tag}",
                text_color=COLORS["primary"],
            )
        else:
            self._active_profile = None
            self._active_profile_key = None
            self.profile_label.configure(
                text="❌ Nieznany SN — brak profilu",
                text_color=COLORS["fail"],
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
        self._abort.clear()

        self.test_btn.configure(state="disabled", text="⏳ Test w toku...")
        self.abort_btn.configure(state="normal")
        self.sn_entry.configure(state="disabled")

        self._reset_display()
        self._set_status("Łączenie z HiPotem...", COLORS["primary"])

        threading.Thread(
            target=self._run_thread,
            args=(sn,),
            daemon=True,
        ).start()

    def _abort_test(self):
        """
        Ustawia flagę przerwania.
        Uwaga: obecny HipotController nie przerywa fizycznie testu w środku komendy,
        więc abort zostanie obsłużony po powrocie z run_full_sequence().
        """
        self._abort.set()
        self.abort_btn.configure(state="disabled", text="⏹ Przerywanie...")
        self._set_status(
            "⚠ Przerwanie testu — czekam na zakończenie kroku...",
            COLORS["warning"],
        )

    def _run_thread(self, sn: str):
        config = load_config()
        serial_cfg = config.get("serial", {})

        ctrl = HipotController(
            port=serial_cfg.get("port", "COM11"),
            baudrate=serial_cfg.get("baudrate", 9600),
            timeout=serial_cfg.get("timeout", 3),
            relay_port=serial_cfg.get("relay_port", None),
        )

        test_start = datetime.now(timezone.utc)

        try:
            ctrl.connect()
            self.after(0, self._set_status, "Test uruchomiony...", COLORS["primary"])

            if self._abort.is_set():
                self.after(
                    0,
                    self._set_status,
                    "⚠ Test anulowany przed startem.",
                    COLORS["warning"],
                )
                return

            seq = ctrl.run_full_sequence(self._active_profile)

            test_end = datetime.now(timezone.utc)

            hipot_result = seq.get("hipot", {})
            gnd_result = seq.get("gnd")

            if self._abort.is_set():
                self.after(
                    0,
                    self._set_status,
                    "⚠ Test przerwany przez operatora.",
                    COLORS["warning"],
                )
                self.after(
                    0,
                    self.result_label.configure,
                    {"text": "ABORT", "text_color": COLORS["warning"]},
                )
                return

            operator = f"{self.hrid} {self.user.get('name', '')}".strip()
            profile_key = self._active_profile_key or ""

            ted_status = {"ok": False, "error": "TED nie został uruchomiony"}
            csv_path = ""

            try:
                payload = build_hipot_payload(
                    sn=sn,
                    operator=operator,
                    profile_key=profile_key,
                    hipot=hipot_result,
                    gnd=gnd_result,
                    start_time=test_start,
                    end_time=test_end,
                    csv_path="",
                )

                # Na razie wysyłamy do tabel testowych TED.
                # Produkcja dopiero po potwierdzeniu z IT: db_type=""
                ted_status = send_to_ted(payload, db_type="TEST")

            except Exception as e:
                ted_status = {
                    "ok": False,
                    "error": f"Błąd przygotowania/wysyłki TED: {e}",
                }

            try:
                csv_path = save_csv_result(
                    log_dir="logs",
                    sn=sn,
                    operator=operator,
                    profile_name=profile_key,
                    hipot=hipot_result,
                    gnd=gnd_result,
                    ted_status=ted_status,
                )
                print(f"CSV zapisany: {csv_path}")
            except Exception as e:
                print(f"❌ Błąd zapisu CSV: {e}")

            print(f"TED status: {ted_status}")

            self.after(
                0,
                self._show_result,
                sn,
                hipot_result,
                gnd_result,
                ted_status,
                csv_path,
            )

        except Exception as e:
            self.after(0, self._set_status, f"❌ {e}", COLORS["fail"])
            self.after(
                0,
                self.result_label.configure,
                {"text": "ERROR", "text_color": COLORS["fail"]},
            )

        finally:
            ctrl.disconnect()
            self._running = False
            self.after(0, self._restore_ui)

    def _restore_ui(self):
        """Przywraca UI po zakończeniu lub przerwaniu testu."""
        self.test_btn.configure(state="normal", text="▶ START TEST")
        self.abort_btn.configure(state="disabled", text="⏹ ABORT")
        self.sn_entry.configure(state="normal")
        self.sn_entry.delete(0, "end")
        self.sn_entry.focus()

    def _ted_suffix(self, ted_status: dict | None) -> str:
        if ted_status is None:
            return ""

        if ted_status.get("ok"):
            return " | TED: OK"

        return " | TED: BŁĄD"

    def _show_result(
        self,
        sn: str,
        r: dict,
        gnd: dict = None,
        ted_status: dict | None = None,
        csv_path: str = "",
    ):
        error = r.get("error")
        result = r.get("result", "")
        ted_suffix = self._ted_suffix(ted_status)

        # ── HiPot wynik ────────────────────────────────────────────────────
        if error:
            self.result_label.configure(text="ERROR", text_color=COLORS["fail"])
            self._set_status(f"❌ {error}{ted_suffix}", COLORS["fail"])

        elif result == "Pass":
            self.result_label.configure(text="✔ PASS", text_color=COLORS["success"])
            self._set_status(f"✔ PASS | SN: {sn}{ted_suffix}", COLORS["success"])

        elif result == "Fail":
            self.result_label.configure(text="✘ FAIL", text_color=COLORS["fail"])
            desc = r.get("error_desc", "")
            self._set_status(
                f"✘ FAIL | SN: {sn}{' | ' + desc if desc else ''}{ted_suffix}",
                COLORS["fail"],
            )

        else:
            status = r.get("status", "").upper()
            self.result_label.configure(
                text=f"⚠ {status}",
                text_color=COLORS["warning"],
            )
            self._set_status(f"⚠ {status} | SN: {sn}{ted_suffix}", COLORS["warning"])

        self.volt_lbl.configure(text=f"{r.get('voltage', '—')} kV")
        self.curr_lbl.configure(text=f"{r.get('current', '—')} mA")
        self.time_lbl.configure(text=f"{r.get('time', '—')} s")

        # ── Ground Bond wynik, jeśli był ───────────────────────────────────
        if gnd is not None:
            self.gnd_frame.grid()

            gnd_verdict = gnd.get("result", "")
            gnd_error = gnd.get("error")

            if gnd_error:
                self.gnd_result_lbl.configure(
                    text="❌ ERROR",
                    text_color=COLORS["fail"],
                )
                self._set_status(
                    f"❌ GND Error: {gnd_error}{ted_suffix}",
                    COLORS["fail"],
                )

            elif gnd_verdict == "Pass":
                self.gnd_result_lbl.configure(
                    text="✔ GND PASS",
                    text_color=COLORS["success"],
                )

            elif gnd_verdict == "Fail":
                self.gnd_result_lbl.configure(
                    text="✘ GND FAIL",
                    text_color=COLORS["fail"],
                )
                desc = gnd.get("error_desc", "")
                self._set_status(
                    f"✘ GND FAIL | SN: {sn}{' | ' + desc if desc else ''}{ted_suffix}",
                    COLORS["fail"],
                )

            else:
                self.gnd_result_lbl.configure(
                    text="⚠ GND ?",
                    text_color=COLORS["warning"],
                )

            self.gnd_res_lbl.configure(text=f"{gnd.get('resistance', '—')} mΩ")
            self.gnd_curr_lbl.configure(text=f"{gnd.get('current', '—')} A")
            self.gnd_time_lbl.configure(text=f"{gnd.get('time', '—')} s")

        # ── Diagnostyka TED / CSV do konsoli ───────────────────────────────
        if ted_status is not None and not ted_status.get("ok"):
            print(f"❌ TED error: {ted_status.get('error', '')}")

        if csv_path:
            print(f"Log CSV: {csv_path}")