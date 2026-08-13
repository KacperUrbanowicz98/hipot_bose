"""
main_screen.py
--------------
Ekran operatora.

NAJWAŻNIEJSZA ZMIANA — przyczyna zgłoszenia "HiPot pass, Ground Bond fail,
wynik końcowy pass":

Duża etykieta wyniku była wcześniej ustawiana WYŁĄCZNIE na podstawie wyniku
HiPot. Ground Bond trafiał do osobnej, małej etykiety i do paska statusu, ale
nic nie cofało dużego zielonego "✔ PASS". Operator widział dominujący element
ekranu i przepuszczał sztukę.

Teraz duża etykieta pokazuje WERDYKT ZBIORCZY z modułu verdict, liczony
fail-safe: PASS tylko wtedy, gdy każdy wykonany krok zwrócił jawne "Pass".
UNKNOWN i ABORTED nigdy nie są zielone.

Pozostałe zmiany:
  - configure() wołane przez lambda; wcześniej słownik przekazywany
    pozycyjnie trafiał do require_redraw i etykiety ERROR/ABORT w ogóle
    się nie wyświetlały,
  - ABORT realnie przerywa test (threading.Event trafia do kontrolera),
    a wynik przerwanego testu JEST zapisywany zamiast być odrzucany,
  - błąd zapisu CSV blokuje ekran zamiast trafiać do print() bez konsoli,
  - flaga runtime_state, żeby panel inżynieryjny nie mógł przełączać
    przekaźnika w trakcie testu.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from tkinter import messagebox

import customtkinter as ctk

import runtime_state
import verdict as V
from app_logging import get_logger
from config import COLORS, load_config, resolve_profile_for_sn
from hipot_controller import HipotController
from result_logger import ResultLogError, save_result as save_csv_result
from ted_client import build_hipot_payload, flush_spool, send_to_ted

log = get_logger(__name__)


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

    # ══════════════════════════════════════════════════════════════════════
    # Budowa UI
    # ══════════════════════════════════════════════════════════════════════
    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(
            self, fg_color=COLORS["surface"], corner_radius=0, height=52,
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
            text=f"👤 {self.user.get('name', '')} | {self.hrid} | "
                 f"{str(self.user.get('role', '')).upper()}",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        ).grid(row=0, column=1, padx=20)

        ctk.CTkButton(
            header,
            text="Wyloguj",
            width=90, height=30,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            hover_color=COLORS["bg"],
            command=self._logout,
        ).grid(row=0, column=2, padx=20)

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
            height=42, corner_radius=8,
            border_color=COLORS["border"],
        )
        self.sn_entry.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.sn_entry.bind("<Return>", lambda e: self._start_test())
        self.sn_entry.bind("<KeyRelease>", lambda e: self._on_sn_change())

        self.profile_label = ctk.CTkLabel(
            body, text="Profil: —",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"], anchor="w",
        )
        self.profile_label.grid(row=2, column=0, sticky="w", pady=(0, 20))

        # ── Duża etykieta: WERDYKT ZBIORCZY ───────────────────────────────
        self.result_label = ctk.CTkLabel(
            body, text="—",
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color=COLORS["muted"],
        )
        self.result_label.grid(row=3, column=0, pady=(0, 2))

        # Podpis pod werdyktem — mówi wprost, czy wolno zwolnić sztukę.
        self.release_label = ctk.CTkLabel(
            body, text="",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["muted"],
        )
        self.release_label.grid(row=4, column=0, pady=(0, 8))

        details = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        details.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        details.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(
            details, text="HiPot",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, columnspan=3, pady=(8, 0))

        self.hipot_result_lbl = ctk.CTkLabel(
            details, text="—",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["muted"],
        )
        self.hipot_result_lbl.grid(row=1, column=0, columnspan=3, pady=(2, 4))

        self.volt_lbl = self._detail_cell(details, "Napięcie", "—", 0)
        self.curr_lbl = self._detail_cell(details, "Prąd", "—", 1)
        self.time_lbl = self._detail_cell(details, "Czas", "—", 2)

        self.gnd_frame = ctk.CTkFrame(body, fg_color=COLORS["card"], corner_radius=10)
        self.gnd_frame.grid(row=6, column=0, sticky="ew", pady=(0, 16))
        self.gnd_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.gnd_frame.grid_remove()

        ctk.CTkLabel(
            self.gnd_frame, text="Ground Bond",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, columnspan=3, pady=(8, 0))

        self.gnd_result_lbl = ctk.CTkLabel(
            self.gnd_frame, text="—",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["muted"],
        )
        self.gnd_result_lbl.grid(row=1, column=0, columnspan=3, pady=(2, 4))

        self.gnd_res_lbl = self._detail_cell(self.gnd_frame, "Rezystancja", "—", 0)
        self.gnd_curr_lbl = self._detail_cell(self.gnd_frame, "Prąd", "—", 1)
        self.gnd_time_lbl = self._detail_cell(self.gnd_frame, "Czas", "—", 2)

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.grid(row=7, column=0, sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)

        self.test_btn = ctk.CTkButton(
            btn_row,
            text="▶ START TEST",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52, corner_radius=10,
            fg_color=COLORS["primary"], hover_color="#005a9e",
            command=self._start_test,
        )
        self.test_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.abort_btn = ctk.CTkButton(
            btn_row,
            text="⏹ ABORT",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52, width=130, corner_radius=10,
            fg_color=COLORS["fail"], hover_color="#8b0000",
            state="disabled",
            command=self._abort_test,
        )
        self.abort_btn.grid(row=0, column=1)

        self.status_lbl = ctk.CTkLabel(
            body, text="Gotowy",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        self.status_lbl.grid(row=8, column=0, pady=(8, 0))

    def _detail_cell(self, parent, label, value, col):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=2, column=col, padx=8, pady=12, sticky="ew")

        ctk.CTkLabel(
            f, text=label,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        ).pack()

        val = ctk.CTkLabel(
            f, text=value,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"],
        )
        val.pack()

        return val

    # ══════════════════════════════════════════════════════════════════════
    # Pomocnicze UI
    # ══════════════════════════════════════════════════════════════════════
    def _set_status(self, msg, color=None):
        self.status_lbl.configure(text=msg, text_color=color or COLORS["muted"])

    def _set_verdict_label(self, text: str, color: str):
        """Zawsze przez tę metodę — nigdy configure() ze słownikiem pozycyjnie."""
        self.result_label.configure(text=text, text_color=color)

    def _reset_display(self):
        self._set_verdict_label("—", COLORS["muted"])
        self.release_label.configure(text="", text_color=COLORS["muted"])

        self.hipot_result_lbl.configure(text="—", text_color=COLORS["muted"])
        self.volt_lbl.configure(text="—")
        self.curr_lbl.configure(text="—")
        self.time_lbl.configure(text="—")

        self.gnd_frame.grid_remove()
        self.gnd_result_lbl.configure(text="—", text_color=COLORS["muted"])
        self.gnd_res_lbl.configure(text="—")
        self.gnd_curr_lbl.configure(text="—")
        self.gnd_time_lbl.configure(text="—")

    def _logout(self):
        if self._running:
            messagebox.showwarning(
                "Test w toku",
                "Nie można się wylogować w trakcie testu.",
                parent=self,
            )
            return
        self.on_logout()

    # ══════════════════════════════════════════════════════════════════════
    # SN / profil
    # ══════════════════════════════════════════════════════════════════════
    def _on_sn_change(self):
        sn = self.sn_entry.get().strip()

        if len(sn) < 4:
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

            gnd_tag = " | 🔗 GND" if profile.get("ground_bond") is not None else ""

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

    @property
    def _expects_gnd(self) -> bool:
        """Czy aktywny profil wymaga Ground Bond."""
        return bool((self._active_profile or {}).get("ground_bond"))

    # ══════════════════════════════════════════════════════════════════════
    # Start / abort
    # ══════════════════════════════════════════════════════════════════════
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
        runtime_state.set_test_running(True, sn)

        self.test_btn.configure(state="disabled", text="⏳ Test w toku...")
        self.abort_btn.configure(state="normal", text="⏹ ABORT")
        self.sn_entry.configure(state="disabled")

        self._reset_display()
        self._set_status("Łączenie z HiPotem...", COLORS["primary"])

        threading.Thread(
            target=self._run_thread, args=(sn,), daemon=True,
        ).start()

    def _abort_test(self):
        """
        Ustawia flagę przerwania.

        HipotController sprawdza ją między komendami i w pętlach czekania,
        a przy przerwaniu wysyła STOP + RESET i wraca przekaźnikiem na HIPOT.
        """
        log.warning("ABORT wciśnięty przez operatora %s", self.hrid)
        self._abort.set()
        self.abort_btn.configure(state="disabled", text="⏹ Przerywanie...")
        self._set_status(
            "⚠ Przerywanie testu — zdejmuję napięcie...",
            COLORS["warning"],
        )

    # ══════════════════════════════════════════════════════════════════════
    # Wątek testu
    # ══════════════════════════════════════════════════════════════════════
    def _run_thread(self, sn: str):
        config = load_config()
        serial_cfg = config.get("serial", {})
        integrations_cfg = config.get("integrations", {})
        hipot_cfg = config.get("hipot", {})

        ted_enabled = integrations_cfg.get("ted_enabled", False)
        ted_db_type = integrations_cfg.get("ted_db_type", "TEST")

        expects_gnd = self._expects_gnd

        ctrl = HipotController(
            port=serial_cfg.get("port", "COM11"),
            baudrate=serial_cfg.get("baudrate", 9600),
            timeout=serial_cfg.get("timeout", 3),
            relay_port=serial_cfg.get("relay_port", None),
            abort_event=self._abort,
            hipot_cfg=hipot_cfg,
        )

        test_start = datetime.now(timezone.utc)

        hipot_result: dict = {}
        gnd_result: dict | None = None
        csv_path = ""
        csv_error = ""

        ted_status = {
            "ok": False,
            "skipped": True,
            "queued": False,
            "error": "",
            "message": "TED disabled in config",
        }

        try:
            # Zaległe wysyłki z poprzednich testów — tanie, gdy kolejka pusta.
            if ted_enabled:
                try:
                    flushed = flush_spool(log_dir="logs")
                    if flushed.get("sent"):
                        log.info("TED: wysłano %d zaległych rekordów",
                                 flushed["sent"])
                except Exception as e:
                    log.warning("TED: flush kolejki nieudany: %s", e)

            ctrl.connect()
            self.after(0, self._set_status, "Test uruchomiony...", COLORS["primary"])

            seq = ctrl.run_full_sequence(self._active_profile)

            test_end = datetime.now(timezone.utc)

            hipot_result = seq.get("hipot", {}) or {}
            gnd_result = seq.get("gnd")

            aborted = bool(
                self._abort.is_set()
                or hipot_result.get("aborted")
                or (gnd_result or {}).get("aborted")
            )

            overall = V.compute_overall(
                hipot_result, gnd_result,
                expects_gnd=expects_gnd, aborted=aborted,
            )

            operator = f"{self.hrid} {self.user.get('name', '')}".strip()
            profile_key = self._active_profile_key or ""

            log.info("SN=%s | werdykt=%s | hipot=%s | gnd=%s",
                     sn, overall, hipot_result.get("result"),
                     (gnd_result or {}).get("result"))

            # ── TED ───────────────────────────────────────────────────────
            if ted_enabled:
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
                        expects_gnd=expects_gnd,
                        aborted=aborted,
                    )

                    ted_status = send_to_ted(payload, db_type=ted_db_type)

                except Exception as e:
                    log.exception("Błąd przygotowania/wysyłki TED")
                    ted_status = {
                        "ok": False, "skipped": False, "queued": False,
                        "error": f"Błąd przygotowania/wysyłki TED: {e}",
                    }
            else:
                log.info("TED wyłączony w konfiguracji — zapis tylko lokalny CSV.")

            # ── CSV ───────────────────────────────────────────────────────
            # Zapisujemy ZAWSZE, także wynik przerwany. Wcześniej abort
            # powodował return przed zapisem: sztuka fizycznie przetestowana,
            # rekord nie powstawał nigdzie.
            try:
                csv_path = save_csv_result(
                    log_dir="logs",
                    sn=sn,
                    operator=operator,
                    profile_name=profile_key,
                    hipot=hipot_result,
                    gnd=gnd_result,
                    ted_status=ted_status,
                    expects_gnd=expects_gnd,
                    aborted=aborted,
                )
            except ResultLogError as e:
                csv_error = str(e)
                log.error("BŁĄD zapisu CSV: %s", e)
            except Exception as e:
                csv_error = f"Nieoczekiwany błąd zapisu CSV: {e}"
                log.exception("Nieoczekiwany błąd zapisu CSV")

            self.after(
                0, self._show_result,
                sn, hipot_result, gnd_result, ted_status,
                csv_path, csv_error, overall,
            )

        except Exception as e:
            log.exception("Błąd wątku testowego")
            message = str(e)
            self.after(0, self._show_fatal, sn, message)

        finally:
            try:
                ctrl.disconnect()
            except Exception as e:
                log.warning("Rozłączenie testera nieudane: %s", e)

            runtime_state.set_test_running(False)
            self.after(0, self._restore_ui)

    def _restore_ui(self):
        self._running = False
        self.test_btn.configure(state="normal", text="▶ START TEST")
        self.abort_btn.configure(state="disabled", text="⏹ ABORT")
        self.sn_entry.configure(state="normal")
        self.sn_entry.delete(0, "end")
        self.sn_entry.focus()

    # ══════════════════════════════════════════════════════════════════════
    # Prezentacja wyniku
    # ══════════════════════════════════════════════════════════════════════
    def _show_fatal(self, sn: str, message: str):
        """Awaria przed uzyskaniem jakiegokolwiek wyniku."""
        self._set_verdict_label(*_display(V.ERROR))
        self.release_label.configure(
            text="⛔ NIE ZWALNIAJ SZTUKI", text_color=COLORS["fail"]
        )
        self._set_status(f"❌ {message}", COLORS["fail"])

        messagebox.showerror(
            "Błąd testu",
            f"SN: {sn}\n\n{message}\n\nSztuki nie wolno zwolnić.",
            parent=self,
        )

    def _ted_suffix(self, ted_status: dict | None) -> str:
        if not ted_status or ted_status.get("skipped"):
            return ""

        if ted_status.get("ok"):
            return " | TED: OK"

        if ted_status.get("queued"):
            return " | TED: W KOLEJCE"

        return " | TED: BŁĄD"

    def _show_result(
        self,
        sn: str,
        hipot: dict,
        gnd: dict | None,
        ted_status: dict | None,
        csv_path: str,
        csv_error: str,
        overall: str,
    ):
        ted_suffix = self._ted_suffix(ted_status)

        # ── 1. WERDYKT ZBIORCZY steruje dużą etykietą ─────────────────────
        text, color = _display(overall)
        self._set_verdict_label(text, color)

        if V.is_releasable(overall):
            self.release_label.configure(
                text="Sztuka może iść dalej", text_color=COLORS["success"]
            )
        else:
            self.release_label.configure(
                text="⛔ NIE ZWALNIAJ SZTUKI", text_color=COLORS["fail"]
            )

        # ── 2. Szczegóły HiPot ────────────────────────────────────────────
        hipot_v = V.step_verdict(hipot)
        h_text, h_color = _display(hipot_v)
        self.hipot_result_lbl.configure(text=h_text, text_color=h_color)

        self.volt_lbl.configure(text=f"{hipot.get('voltage', '—')} kV")
        self.curr_lbl.configure(text=f"{hipot.get('current', '—')} mA")
        self.time_lbl.configure(text=f"{hipot.get('time', '—')} s")

        # ── 3. Szczegóły Ground Bond ──────────────────────────────────────
        if gnd is not None or self._expects_gnd:
            self.gnd_frame.grid()

            if gnd is None:
                self.gnd_result_lbl.configure(
                    text="⚠ NIE WYKONANO", text_color=COLORS["warning"]
                )
            else:
                gnd_v = V.step_verdict(gnd)
                g_text, g_color = _display(gnd_v)
                self.gnd_result_lbl.configure(text=g_text, text_color=g_color)

                self.gnd_res_lbl.configure(text=f"{gnd.get('resistance', '—')} mΩ")
                self.gnd_curr_lbl.configure(text=f"{gnd.get('current', '—')} A")
                self.gnd_time_lbl.configure(text=f"{gnd.get('time', '—')} s")

                if gnd.get("fields_ambiguous"):
                    log.warning("GND: kolejność pól prąd/rezystancja niepotwierdzona.")
        else:
            self.gnd_frame.grid_remove()

        # ── 4. Pasek statusu ──────────────────────────────────────────────
        detail = _first_message(hipot, gnd)
        detail_suffix = f" | {detail}" if detail else ""

        self._set_status(f"{text} | SN: {sn}{detail_suffix}{ted_suffix}", color)

        # ── 5. Zapis lokalny — awaria musi być widoczna ───────────────────
        if csv_error:
            self.release_label.configure(
                text="⛔ WYNIK NIE ZOSTAŁ ZAPISANY", text_color=COLORS["fail"]
            )
            self._set_status(f"❌ {csv_error}", COLORS["fail"])

            messagebox.showerror(
                "Błąd zapisu wyniku",
                f"SN: {sn}\nWerdykt: {overall}\n\n{csv_error}\n\n"
                "Wynik testu NIE został zapisany. Zgłoś to przełożonemu "
                "przed kontynuowaniem pracy.",
                parent=self,
            )
        elif csv_path:
            log.info("Log CSV: %s", csv_path)

        # ── 6. Wynik niejednoznaczny wymaga potwierdzenia ─────────────────
        if overall in (V.UNKNOWN, V.ERROR):
            messagebox.showwarning(
                "Wynik niejednoznaczny",
                f"SN: {sn}\nWerdykt: {overall}\n\n"
                f"{detail or 'Tester nie zwrócił jednoznacznego wyniku.'}\n\n"
                "Sztuki nie wolno zwolnić. Powtórz test lub zgłoś do inżyniera.",
                parent=self,
            )

        if ted_status and ted_status.get("queued"):
            log.warning("TED: rekord SN=%s czeka w kolejce na wysyłkę.", sn)


# ══════════════════════════════════════════════════════════════════════════
# Helpers modułowe
# ══════════════════════════════════════════════════════════════════════════
def _display(overall: str) -> tuple[str, str]:
    """Zamienia werdykt na (tekst, kolor RGB) z palety aplikacji."""
    text, color_key = V.display_for(overall)
    return text, COLORS.get(color_key, COLORS["muted"])


def _first_message(hipot: dict | None, gnd: dict | None) -> str:
    """Pierwszy sensowny komunikat błędu z obu kroków."""
    for source in (hipot, gnd):
        if not source:
            continue
        for key in ("error_desc", "error"):
            value = source.get(key)
            if value:
                return str(value)
    return ""
