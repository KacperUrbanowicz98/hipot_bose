# HiPot Bose 1.1.1 — poprawki po pierwszym uruchomieniu

Zgłoszone: wartości GND nieodczytywane, znikające pole GND, 30 s czekania,
mylący napis "NIE ZWALNIAJ SZTUKI". Plus jedna rzecz znaleziona w firmware.

---

## 1.1.1 — co się zmieniło

### A. REGRES: wynik Ground Bond szukany znowu w dwóch slotach

**To był mój błąd w 1.1.0.** Wersja 1.1.0 odpytywała tylko `RD 2?`.
Poprzedni kod sprawdzał **oba** slots i miał na to wyraźny komentarz:

> *„Slaughter 4320: wynik GND trafia pod RD 1? (nie RD 2?) gdy TEST był
> wysłany osobno — szukamy w obu slotach"*

Usunąłem ten fallback przy przepisywaniu na polling. Efekt: `RD 2?` zwracał
NAK aż do wyczerpania marginesu — stąd **jednocześnie** brak odczytanych
wartości GND **i** 30 sekund czekania. Jedna przyczyna, dwa objawy.

Teraz każdy przebieg pollingu sprawdza `RD 2?`, potem `RD 1?`, i akceptuje
pierwszy rekord, który jest wynikiem Ground Bond. Rekord HiPot w slocie GND
jest odrzucany, nie brany za wynik.

### B. Parsowanie po pozycji werdyktu, nie po sztywnych indeksach

Zamiast liczyć od początku rekordu, kod szuka pola `Pass`/`Fail` i liczy
pozostałe **względem niego**. Obsłużone warianty:

| Rekord | Opis |
|---|---|
| `1,2,GND,Pass,25.1,65,1.0` | 7 pól, znacznik w środku |
| `GND,2,Pass,25.1,65,1.0` | 6 pól, znacznik z przodu |
| `1,2,Pass,25.1,65,1.0` | 6 pól, **bez** znacznika |
| `2,Pass,25.1,65,1.0` | 5 pól |

Poprzednia wersja przy każdym odstępstwie od dwóch znanych formatów zwracała
`Unknown`. Rekord bez znacznika `GND` jest przyjmowany po 3 pełnych
przebiegach (`sweeps_before_fallback`) z ostrzeżeniem w logu.

### C. Heartbeat do ESP — znalezione w arduino2.txt

Firmware ma `WATCHDOG_TIMEOUT_MS 30000`: po 30 s bez komendy ESP **sam wraca
na HIPOT**. Podczas Ground Bond ostatnią komendą do ESP było `STATUS?` zaraz
po `set_pe()`, a potem aplikacja nie wysyłała nic przez cały czas testu.

Przy 30-sekundowym marginesie watchdog trafiał **w trakcie testu GND** —
przełączenie styków przekaźnika pod prądem 25 A. Teraz w każdej pętli
czekania leci `PING` co 2 s. Firmware zostaje bez zmian.

### D. Krótsze marginesy

| Parametr | Było | Jest |
|---|---|---|
| `result_margin_s` (HiPot) | 30 s | **10 s** |
| `gnd_result_margin_s` (GND) | — | **6 s** |
| `result_poll_interval_s` | 0.5 s | 0.3 s |

Margines to limit awaryjny doliczany do `ramp+dwell`, **nie** czas czekania.
Polling zwraca wynik natychmiast, gdy tester go odda. Oba do edycji
w Panelu → Konfiguracja portu → Parametry czasowe testu.

### E. Pole z wynikiem GND już nie znika

`_on_sn_change()` wołało `gnd_frame.grid_remove()`, a `_restore_ui()` po
teście czyści pole SN i ustawia tam fokus — więc pierwsze dotknięcie
klawiatury kasowało ramkę z wynikiem Ground Bond.

Teraz `_on_sn_change()` dotyka **wyłącznie** etykiety profilu. Obszar wyniku
czyści tylko `_reset_display()` przy starcie następnego testu.

### F. "NIE ZWALNIAJ SZTUKI" zastąpione konkretnym poleceniem

Stary napis nie mówił ani dlaczego, ani co zrobić — i sugerował, że sztuka
jest zła także wtedy, gdy to **test** się nie udał. Teraz:

| Werdykt | Podpis pod wynikiem |
|---|---|
| PASS | Wynik pozytywny — sztuka może przejść do następnej operacji |
| FAIL | Sztuka NIE przeszła testu — oznacz jako NOK i postępuj wg instrukcji stanowiskowej |
| ERROR | Test nie został wykonany do końca — sprawdź stanowisko i POWTÓRZ test. **To nie jest wynik NOK sztuki** |
| UNKNOWN | Tester nie zwrócił jednoznacznego wyniku — POWTÓRZ test. Jeśli powtarza się, zgłoś inżynierowi |
| ABORTED | Test przerwany przed zakończeniem — POWTÓRZ test |

Rozróżnienie FAIL / ERROR jest tu najważniejsze: FAIL to zła sztuka,
ERROR i UNKNOWN to nieudany test. Wcześniej operator widział to samo
ostrzeżenie w obu przypadkach.

Teksty można dopasować do instrukcji stanowiskowej **bez przebudowy EXE**:

```json
"ui": {
    "verdict_hints": {
        "FAIL": "Odłóż na czerwoną paletę, karta 4B"
    }
}
```

### G. Timeouty przekaźnika dopasowane do firmware

Z `arduino2.txt` wyliczone: `handlePe()` w happy path odpowiada po ~0.67 s,
ale przy 3 nieudanych próbach + `safeReturnToHipot()` finalna odpowiedź
przychodzi po **~3.34 s**. Aplikacja czekała 2.0 s, więc w ścieżce błędu
kończyła odczyt w połowie retry ESP i widziała `WARN:PE_VERIFY_FAIL`
zamiast werdyktu. Timeout podniesiony do 4.5 s, a linie `WARN:*` są teraz
jawnie pomijane jako informacyjne.

### H. Log RS-232 na poziomie INFO

Każda komenda i surowa odpowiedź trafiają do `logs/app.log`. Szczegóły
w `DIAGNOSTYKA.md` — **konsoli w buildzie `--windowed` nie ma**, plik logu
jest jedynym źródłem.

---

## 1.1.1 — uwaga o hardware (do rozważenia, nie zmieniałem)

`CHECK_PIN` jest ustawiony jako `INPUT_PULLUP`, a `LOW = PE`, `HIGH = HIPOT`.
Urwany albo odłączony przewód czujnika daje przez pullup `HIGH`, czyli
**„jestem w bezpiecznej pozycji HIPOT"** — mimo że przekaźnik może fizycznie
siedzieć na PE. To fail-*unsafe* kierunek dla detekcji zaklinowanego styku.

Odwrócenie logiki (PE = HIGH) sprawiłoby, że urwany przewód czyta się jako
„nie jestem w HIPOT" i blokuje test. Komentarz w firmware sugeruje też
przeniesienie `CHECK_PIN` z `D3` (pin bootowy GPIO0) na `D5/D6/D7` — warto
zrobić obie rzeczy przy najbliższej okazji serwisowej.

---

# HiPot Bose 1.1.0 — poprawki bezpieczeństwa i obsługi błędów (baza)

Komplet plików drop-in. Podmieniasz pliki w katalogu projektu, budujesz
`python create_exe.py`, wdrażasz cały folder `dist/HiPot Bose`.

---

## 1. Co zamyka zgłoszenie

**Objaw:** HiPot PASS, Ground Bond FAIL, a wynik końcowy na ekranie pokazał PASS.

**Przyczyna:** w `main_screen._show_result()` duża etykieta (42 pt) była
ustawiana wyłącznie na podstawie wyniku HiPot. Ground Bond trafiał do osobnej
małej etykiety i do paska statusu — nic nie cofało zielonego `✔ PASS`.

**Dane były poprawne.** `result_logger` i `ted_client` liczyły werdykt
niezależnie i zapisały FAIL. Błąd był wyłącznie w tym, co widział operator.

**Poprawka:** nowy moduł `verdict.py` — jedno źródło prawdy dla trzech miejsc,
które wcześniej miały własne kopie logiki. Zasada fail-safe: PASS tylko wtedy,
gdy **każdy wykonany krok** zwrócił jawne `Pass`. `UNKNOWN` i `ABORTED` nigdy
nie są zielone.

Zamknięty został też cichszy wariant tego samego błędu: gdy Ground Bond
zwracał coś innego niż dokładnie `"Fail"` (np. `Unknown` po NAK), stara gałąź
`else` nie dotykała paska statusu — na ekranie zostawało zielone PASS na górze
i na dole, a jedynym sygnałem było małe `⚠ GND ?`.

---

## 2. Lista plików

### Nowe

| Plik | Rola |
|---|---|
| `verdict.py` | Werdykt zbiorczy — jedno źródło prawdy dla UI, CSV i TED |
| `app_logging.py` | Logowanie do `logs/app.log` + audyt `logs/config_audit.log` |
| `runtime_state.py` | Flaga „trwa test" widoczna dla panelu inżynieryjnego |
| `set_engineer_password.py` | Narzędzie serwisowe do ustawienia hasła (nie pakowane do EXE) |
| `config.example.json` | Wzorzec konfiguracji z nowymi sekcjami |

### Zmienione

`main.py`, `main_screen.py`, `config.py`, `hipot_controller.py`,
`relay_controller.py`, `result_logger.py`, `ted_client.py`,
`engineer_panel.py`, `login_screen.py`, `password_dialog.py`, `create_exe.py`

### Do usunięcia z repo

`logger.py` — martwy kod zastąpiony przez `result_logger.py`. Budował nazwę
pliku z niesanityzowanego SN (`f"{sn}_{timestamp}.txt"`), więc SN ze znakami
ścieżki zapisywałby poza `logs/`. Nie jest już w `PROJECT_FILES`.

---

## 3. Zmiany bezpieczeństwa elektrycznego

**Czekanie na koniec testu.** Zniknęło `min(ramp + dwell + 1.5, test_timeout)`,
które przy długim profilu **skracało** czekanie i pozwalało czytać wynik przy
podanym napięciu. Teraz: minimum `ramp + dwell` (fizyka), potem polling
`RD n?` aż do wyniku. Tester zwraca NAK, dopóki wyniku nie ma, więc polling
sam w sobie potwierdza zakończenie testu — bez znajomości rejestru statusu.
`result_margin_s` (domyślnie 30 s) jest **limitem awaryjnym doliczanym** do
czasu testu, nigdy jego skróceniem. Przekroczenie → `STOP` + `RESET` i błąd.

**Przekaźnik.** `set_pe()` idzie dopiero po potwierdzonym odczycie wyniku plus
`relay_switch_delay_s` (domyślnie 1 s). Pozycja HIPOT jest wymuszana **przed
każdym testem**, gdy `relay_port` jest skonfigurowany — także dla profilu bez
Ground Bond. Wcześniej korekta działała tylko dla profili z GND, więc
przekaźnik zostawiony na PE kierował 3 kV w tor PE przy następnej sztuce.

**Brak `relay_port` przy profilu z Ground Bond = twardy błąd.** Wcześniej
sekwencja leciała dalej: 25 A przez tor nieprzełączony na PE, a jedyne
ostrzeżenie to `print()` niewidoczny w buildzie `--windowed`. Wyłączalne:
`hipot.require_relay_for_gnd`.

**ABORT działa naprawdę.** `threading.Event` trafia do kontrolera i jest
sprawdzany między komendami oraz w pętlach czekania. Przerwanie wysyła `STOP`
+ `RESET` i wraca przekaźnikiem na HIPOT. Wynik przerwanego testu **jest
zapisywany** ze statusem `ABORTED` — wcześniej `return` przed zapisem
oznaczał, że sztuka została fizycznie przetestowana, a rekord nie powstawał
nigdzie.

**Wynik inny niż jawny `pass` nie przepuszcza do Ground Bond.** Stary warunek
`status in ("fail", "error")` nie łapał statusu `"done"` z NAK-a i nieznanego
formatu, więc sekwencja szła dalej mimo braku wiarygodnego wyniku HiPot.

**Panel inżynieryjny zablokowany w trakcie testu.** Przyciski `→ PE`,
`→ HIPOT`, `Test połączenia (RESET)` i komendy ręczne sprawdzają
`runtime_state` — dwukrotnie: przy kliknięciu i tuż przed wysłaniem komendy
w wątku. Ręczne `→ PE` samo wraca na HIPOT po zakończeniu.

---

## 4. Obsługa błędów

**Koniec z `print()` w buildzie bez konsoli.** Wszystko idzie do
`logs/app.log` (rotacja 5 MB × 10). `app_logging` podstawia też zaślepkę pod
`sys.stdout`/`sys.stderr`, gdy PyInstaller ustawi je na `None` — pojedynczy
`print()` z biblioteki potrafił wtedy wywrócić wątek.

**Błąd zapisu CSV blokuje ekran.** `save_result()` rzuca `ResultLogError`,
`main_screen` pokazuje czerwone „WYNIK NIE ZOSTAŁ ZAPISANY" i okno modalne.
Wcześniej `print()` bez konsoli oznaczał, że operator widział normalny wynik,
a rekord przepadał (plik otwarty w Excelu, brak uprawnień, pełny dysk).

**`configure()` wołane przez lambda.** Wzorzec
`self.after(0, label.configure, {"text": "ERROR", ...})` przekazywał słownik
**pozycyjnie**. Sygnatura CustomTkinter to `configure(require_redraw=False,
**kwargs)`, więc słownik lądował w `require_redraw`, a etykieta się nie
zmieniała — ścieżki ERROR i ABORT nie wyświetlały nic. W repo nie ma już
żadnego wystąpienia tego wzorca (jest test regresyjny).

**`config.py` odporny.** Uszkodzony `config.json` nie wywraca aplikacji:
plik jest odkładany jako `config.broken_<data>.json`, próbowana jest kopia
`config.json.bak`, a w ostateczności startuje konfiguracja domyślna z wpisem
w logu. Zapis atomowy (`.tmp` + `os.replace`) z kopią poprzedniej wersji —
zanik zasilania nie niszczy już profili. `load_config()` zwraca kopię, więc
mutacja u wywołującego nie psuje cache.

**Prefiksy SN.** Dopasowanie po **najdłuższym pasującym prefiksie**, nie po
`sn[:6]`. Panel dopuszczał 4–8 znaków, a rozwiązywanie sprawdzało dokładnie 6,
więc prefiksy innej długości nigdy nie działały mimo zielonego „✔ Dodano".
Panel ostrzega teraz o zachodzących prefiksach i o mapowaniach wskazujących
na nieistniejący profil.

**Kolejka TED.** Nieudana wysyłka trafia do `logs/ted_queue/*.xml` i jest
ponawiana przy następnym teście. W CSV pojawia się `QUEUED` zamiast cichej
utraty rekordu. Klucz funkcji idzie w nagłówku `x-functions-key`, nie w query
stringu (awaryjnie: `TED_KEY_IN_QUERY=1`), i jest czytany przy każdym
wywołaniu, a nie raz przy imporcie modułu.

---

## 5. Dostęp i audyt

**Hasło inżynieryjne.** `ENG_PASSWORD = "bose2024"` zniknęło z kodu.
W `config.json` jest wyłącznie hash PBKDF2-HMAC-SHA256 (240 000 iteracji).
Lockout: 5 prób → 60 s blokady, licznik przeżywa zamknięcie okna.

*Migracja:* przy pierwszym uruchomieniu aplikacja wpisuje hash
dotychczasowego hasła i podnosi `must_change_password`, żeby wdrożenie nie
odcięło nikogo od panelu. W zakładce **Bezpieczeństwo** jest czerwony baner
i formularz zmiany. **Zmień hasło przy pierwszym uruchomieniu** — hash
bootstrapowy odpowiada znanemu hasłu.

**Log audytowy** `logs/config_audit.log` (append-only, osobna rotacja od
`app.log`): zmiany profili z pełnym „przed → po", zmiany mapowań SN,
użytkowników, portów, parametrów czasowych, ręczne akcje przekaźnika, komendy
diagnostyczne, logowania i próby wejścia do panelu. Podgląd w zakładce
Bezpieczeństwo. Hasła nigdy nie trafiają do audytu.

---

## 6. Nowe sekcje w `config.json`

Aplikacja dopisze brakujące klucze sama przy pierwszym starcie. Pełny wzorzec
w `config.example.json`.

```json
"hipot": {
    "result_margin_s": 30.0,
    "result_poll_interval_s": 0.5,
    "relay_switch_delay_s": 1.0,
    "require_relay_for_gnd": true,
    "gnd_field_order": "auto",
    "gnd_current_tolerance": 0.35,
    "status_query": "SA?",
    "status_busy_tokens": [],
    "status_idle_tokens": []
}
```

### Dwie rzeczy do uzupełnienia na stanowisku

**`status_busy_tokens` / `status_idle_tokens`** — nie znam formatu odpowiedzi
Slaughter 4320 na `SA?`. Dopóki listy są puste, kod opiera się wyłącznie na
pollingu `RD n?`, co działa. Po wpisaniu tokenów dojdzie drugie, niezależne
potwierdzenie stanu testera. Odpowiedź podejrzysz w **Diagnostyka → Test
połączenia** — log wypisuje ją z podpowiedzią.

**`gnd_field_order`** — `hipot_controller.py` i `ground_bond_test.py` opisywały
kolejność pól rezystancja/prąd w `RD n?` **sprzecznie**. Domyślne `"auto"`
porównuje obie liczby z zaprogramowanym prądem GND (np. 25 A) i przypisuje
pola na tej podstawie; przy niejednoznaczności loguje ostrzeżenie i dopisuje
adnotację do opisu błędu. Po weryfikacji w dokumentacji testera ustaw
`"current_first"` albo `"resistance_first"` na sztywno.

To ma znaczenie: przy zamienionych polach `error_desc` porównywał amperaż
z limitem mΩ, więc 25 A nigdy nie przekraczało limitu 100 mΩ i opis awarii
był zawsze ogólny.

---

## 7. Kolejność wdrożenia

1. Podmień pliki, usuń `logger.py`.
2. `python create_exe.py` — builder działa teraz w **trybie ścisłym**: nie
   akceptuje plików typu `main(3).py`. Stare zachowanie: `--allow-numbered`.
   Build diagnostyczny z konsolą: `--console`.
3. Pierwsze uruchomienie: **zmień hasło inżynieryjne** (Panel → Bezpieczeństwo).
4. Sprawdź, czy `serial.relay_port` jest ustawiony — bez niego profile
   z Ground Bond będą teraz blokowane (to celowe).
5. Diagnostyka → Test połączenia → wpisz tokeny `SA?` do `config.json`.
6. Testy na sztuce zastępczej, minimum:
   - HiPot PASS + GND PASS → duże zielone PASS
   - HiPot PASS + GND FAIL → **duże czerwone FAIL** ← to zamyka zgłoszenie
   - HiPot FAIL → GND się nie uruchamia, przekaźnik nie idzie na PE
   - ABORT w trakcie → napięcie zdjęte, wynik zapisany jako ABORTED
   - profil z GND przy pustym `relay_port` → test zablokowany przed `TEST`
   - CSV otwarty w Excelu podczas testu → czerwony komunikat, nie cisza
7. Zweryfikuj kolejność pól GND (punkt 6) i ustaw `gnd_field_order` na sztywno.

---

## 8. Testy wykonane przed przekazaniem

Wszystkie na atrapie testera i przekaźnika — **sprzęt nie był dostępny**.

- **Logika werdyktu (12 przypadków):** zgłoszony scenariusz, GND `Unknown`,
  brak wyniku GND przy profilu wymagającym GND, abort, pisownia `PASS`/`Fail `,
  mapowanie na PASS/FAIL dla TED.
- **Konfiguracja (16):** uszkodzony JSON, zapis atomowy, kopia zapasowa,
  dopasowanie prefiksów 4/5/8-znakowych, izolacja cache, hash hasła,
  zakresy walidacji.
- **CSV i TED (13):** zgodność werdyktu w obu, zera wiodące w SN, wyjątek przy
  błędzie zapisu, `failure_number`, subtesty, klucz w nagłówku, kolejka spool.
- **Parsowanie (9):** wynik GND w slocie HiPot i odwrotnie, śmieci, kody
  błędów, auto-rozpoznanie kolejności pól GND w obie strony.
- **Sekwencja end-to-end (24):** zgłoszony przypadek, kolejność przełączeń
  przekaźnika w czasie, polling zamiast sleepa, blokada przy braku
  `relay_port`, pominięcie GND po FAIL i po wyniku nieczytelnym, ABORT
  z `STOP`, timeout odczytu, korekta pozycji przekaźnika przed testem bez GND.
- **Prezentacja wyniku (22):** na atrapie CustomTkinter wiernie odwzorowującej
  sygnaturę `configure(require_redraw=False, **kwargs)` — potwierdza zarówno
  poprawkę, jak i to, że stary wzorzec był ignorowany. Plus test regresyjny
  szukający `configure({...})` w całym repo.

**Czego nie dało się sprawdzić:** rzeczywistej komunikacji RS-232, formatu
odpowiedzi `SA?` i `RD n?`, zachowania przekaźnika ESP, renderowania UI
w prawdziwym Tk. To wymaga stanowiska.
