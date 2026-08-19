"""
verdict.py
----------
JEDNO źródło prawdy dla werdyktu zbiorczego testu.

Ten moduł istnieje, bo logika "czy sztuka przeszła" była wcześniej
zduplikowana w trzech miejscach:

    main_screen._show_result()          -> co widzi operator
    result_logger.save_result()         -> kolumna result w CSV
    ted_client.build_hipot_payload()    -> pole Result w XML TED

i te trzy implementacje już się rozjeżdżały. Duża etykieta na ekranie
patrzyła wyłącznie na wynik HiPot, przez co HiPot PASS + Ground Bond FAIL
pokazywało operatorowi zielone PASS.

ZASADA FAIL-SAFE
----------------
PASS tylko wtedy, gdy KAŻDY wykonany krok zwrócił jawne "Pass".
Wszystko inne — brak wyniku, nieznany format, błąd komunikacji,
przerwanie przez operatora — NIE jest wynikiem pozytywnym.

Nie ma tu żadnego "domyślnie przepuść".
"""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════════
# STATUSY ZWRACANE PRZEZ SLAUGHTER 4320
# ══════════════════════════════════════════════════════════════════════════
# Tester NIE zwraca słowa "Fail". Zwraca OPISOWY status, np. HI-Limit / OFL.
# Potwierdzone dwoma niezależnymi źródłami:
#
#   1. logs/hipot_log z 2026-08-17 ze stanowiska:
#        hipot_result = Pass | HI-Limit | OFL
#        gnd_result   = Pass | HI-Limit
#
#   2. Slaughter 4000 Series Manual, "Failure Mode Displays" (str. 18-19):
#        HI-Lmt, LO-Lmt, OFL  dla ACW / DCW / IR / GND
#      oraz *STB? BIT 2 = ABORT
#
# Wersja 1.1.1 rozpoznawała wyłącznie 'Pass'/'Fail', więc każda sztuka
# NIEZALICZONA nie była w ogóle akceptowana przez polling — leciał timeout
# i werdykt ERROR zamiast FAIL. To najważniejsza poprawka 1.1.2.
#
# Warianty pisowni: RS-232 oddaje 'HI-Limit', wyświetlacz pokazuje 'HI-Lmt'.
# Trzymamy oba, żeby nie zależeć od wersji firmware.

PASS_TOKENS = ("pass", "passed")

FAIL_TOKENS = (
    # ogólne
    "fail", "failed", "nok",
    # ACW / DCW / IR / GND — przekroczenie limitu górnego
    "hi-limit", "hi-lmt", "hilimit", "hi limit", "hilmt",
    # przekroczenie limitu dolnego (typowo brak kontaktu z DUT)
    "lo-limit", "lo-lmt", "lolimit", "lo limit", "lolmt",
    # przekroczenie zakresu pomiarowego: zwarcie albo przeskok
    "ofl", "overflow",
    # spotykane w innych modelach serii
    "breakdown", "short", "arc", "arc-fail", "ramp-fail", "open",
)

#: Przerwanie testu to NIE zła sztuka — osobna kategoria.
ABORT_TOKENS = ("abort", "aborted")

#: Wszystkie tokeny, po których da się rozpoznać pole werdyktu w rekordzie RD.
ALL_VERDICT_TOKENS = PASS_TOKENS + FAIL_TOKENS + ABORT_TOKENS


# ── Werdykty ───────────────────────────────────────────────────────────────
PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"
UNKNOWN = "UNKNOWN"
ABORTED = "ABORTED"

#: Jedyny werdykt, przy którym wolno zwolnić sztukę dalej.
RELEASABLE = (PASS,)

#: Kolejność ważności — używane, gdy trzeba wybrać "gorszy" z dwóch werdyktów.
_SEVERITY = {
    PASS: 0,
    UNKNOWN: 1,
    ABORTED: 2,
    FAIL: 3,
    ERROR: 4,
}


# ── Normalizacja ───────────────────────────────────────────────────────────
def norm(value) -> str:
    """
    Sprowadza wartość z testera do porównywalnej postaci.

    Tester może zwrócić 'Pass', 'PASS', 'pass ' — wcześniej kod porównywał
    dokładnie == "Pass", więc każda inna pisownia cicho lądowała w gałęzi
    'nieznany wynik'.
    """
    if value is None:
        return ""
    return str(value).strip().lower()


def is_pass(value) -> bool:
    return norm(value) in PASS_TOKENS


def is_fail(value) -> bool:
    """
    True dla KAŻDEGO statusu oznaczającego niezaliczenie — także opisowego
    (HI-Limit, LO-Limit, OFL), bo tester nie zwraca słowa 'Fail'.
    """
    return norm(value) in FAIL_TOKENS


def is_abort(value) -> bool:
    return norm(value) in ABORT_TOKENS


def is_verdict_token(value) -> bool:
    """Czy pole rekordu RD jest polem statusu testu."""
    return norm(value) in ALL_VERDICT_TOKENS


def has_value(value) -> bool:
    """True, jeśli pole niesie jakąkolwiek treść (nie None, nie '', nie '—')."""
    text = norm(value)
    return bool(text) and text not in ("—", "-", "none", "n/a")


# ── Werdykt pojedynczego kroku ─────────────────────────────────────────────
def step_verdict(step: dict | None) -> str:
    """
    Zwraca werdykt pojedynczego kroku (HiPot albo Ground Bond).

    step to słownik zwracany przez HipotController:
        {"result": "Pass"/"Fail"/..., "status": ..., "error": ..., ...}
    """
    if step is None:
        return UNKNOWN

    if step.get("aborted"):
        return ABORTED

    if has_value(step.get("error")):
        return ERROR

    result = step.get("result")

    if is_pass(result):
        return PASS

    # Abort sprawdzany PRZED fail — przerwany test to nie zła sztuka.
    if is_abort(result):
        return ABORTED

    if is_fail(result):
        return FAIL

    # Brak wyniku, 'Unknown', nieoczekiwany format odpowiedzi testera.
    # Świadomie NIE jest to PASS.
    return UNKNOWN


def step_executed(step: dict | None) -> bool:
    """
    Czy krok faktycznie się wykonał (cokolwiek zwrócił)?

    Potrzebne, żeby odróżnić 'Ground Bond pominięty, bo HiPot FAIL'
    od 'Ground Bond wykonany i nie zwrócił wyniku'.
    """
    if step is None:
        return False

    for key in ("result", "resistance", "current", "voltage",
                "time", "error", "error_desc", "raw_result"):
        if has_value(step.get(key)):
            return True

    return False


# ── Werdykt zbiorczy ───────────────────────────────────────────────────────
def compute_overall(
    hipot: dict | None,
    gnd: dict | None,
    expects_gnd: bool = False,
    aborted: bool = False,
) -> str:
    """
    Werdykt zbiorczy całego testu.

    hipot        -> słownik wyniku HiPot
    gnd          -> słownik wyniku Ground Bond albo None
    expects_gnd  -> czy PROFIL wymagał Ground Bond
                    (profile["ground_bond"] is not None)
    aborted      -> czy operator przerwał test

    expects_gnd jest istotne: jeżeli profil wymaga Ground Bond, a wyniku GND
    nie ma w ogóle, to NIE jest PASS — to niekompletny test.
    """
    if aborted:
        return ABORTED

    hipot_v = step_verdict(hipot)

    if hipot_v != PASS:
        return hipot_v

    # HiPot zdał. Teraz Ground Bond.
    if gnd is None:
        if expects_gnd:
            # Profil wymagał GND, a wyniku nie ma. Test niekompletny.
            return UNKNOWN
        # Profil bez GND — HiPot był jedynym krokiem.
        return PASS

    return step_verdict(gnd)


def is_releasable(overall: str) -> bool:
    """Czy przy tym werdykcie wolno zwolnić sztukę dalej."""
    return overall in RELEASABLE


def worst(*verdicts: str) -> str:
    """Zwraca najgorszy z podanych werdyktów."""
    known = [v for v in verdicts if v in _SEVERITY]
    if not known:
        return UNKNOWN
    return max(known, key=lambda v: _SEVERITY[v])


# ── Mapowania na formaty zewnętrzne ────────────────────────────────────────
def to_binary(overall: str) -> str:
    """
    Sprowadza werdykt do PASS/FAIL.

    Systemy zewnętrzne (TED) przyjmują wyłącznie PASS albo FAIL.
    Wszystko, co nie jest jawnym PASS, idzie jako FAIL — fail-safe.
    """
    return PASS if overall == PASS else FAIL


def to_ted_result(overall: str) -> str:
    """Alias dla czytelności w ted_client."""
    return to_binary(overall)


# ── Prezentacja w UI ───────────────────────────────────────────────────────
#: Tekst + klucz koloru z config.COLORS dla dużej etykiety wyniku.
#: UNKNOWN i ABORTED NIGDY nie są zielone.
DISPLAY = {
    PASS:    ("✔ PASS",      "success"),
    FAIL:    ("✘ FAIL",      "fail"),
    ERROR:   ("❌ ERROR",    "fail"),
    UNKNOWN: ("⚠ SPRAWDŹ",   "warning"),
    ABORTED: ("⏹ PRZERWANY", "warning"),
}


def display_for(overall: str) -> tuple[str, str]:
    """Zwraca (tekst, klucz_koloru) dla werdyktu."""
    return DISPLAY.get(overall, DISPLAY[UNKNOWN])
