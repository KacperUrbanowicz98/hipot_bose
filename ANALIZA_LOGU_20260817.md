# Analiza logu ze stanowiska — 2026-08-17

Źródła: `hipot_log_20260817.csv` (10 testów) + Slaughter 4000 Series Manual.

---

## 1. Ten log jest ze STAREJ wersji aplikacji

Plik ma 16 kolumn i **nie ma** `overall_verdict` ani `gnd_expected`, a wiersz
z 13:12 zawiera komunikat `NAK na RD 2? i RD 1? — brak wyniku Ground Bond`,
którego moje wersje nie generują (1.1.0+ pisze `Ground Bond: brak wyniku
w X s. Odpytane slots: ...`).

Wniosek: te 10 testów wykonała aplikacja **przed** moimi zmianami. To dobra
wiadomość diagnostycznie — pokazuje surowe zachowanie testera, niezaburzone
moimi poprawkami. Jeśli po wdrożeniu 1.1.1 masz nowszy `logs/app.log`,
przyślij go osobno; ten CSV odpowiada na inne pytania.

---

## 2. KRYTYCZNE: log ujawnił błąd w mojej wersji 1.1.1

Kolumna `hipot_result` zawiera **`HI-Limit`** i **`OFL`**, a `gnd_result`
zawiera **`HI-Limit`**. Tester **nie zwraca słowa `Fail`** — zwraca opisowy
status. Potwierdza to manual, „Failure Mode Displays" (str. 18-19):
`HI-Lmt`, `LO-Lmt`, `OFL` dla ACW / DCW / IR / GND.

Wersja 1.1.1 rozpoznawała w rekordzie `RD n?` wyłącznie `Pass` i `Fail`.
Konsekwencja była poważna:

| | 1.1.1 (błędnie) | 1.1.2 (poprawnie) |
|---|---|---|
| `HI-Limit` rozpoznany? | nie | tak |
| Polling akceptuje rekord? | **nie** | tak |
| Co widziałby operator | timeout → `❌ ERROR`, „powtórz test" | `✘ FAIL`, „oznacz jako NOK" |

Czyli w 1.1.1 **każda sztuka niezaliczona** kończyłaby się timeoutem,
awaryjnym `STOP` i werdyktem ERROR — operator dostawałby polecenie
powtórzenia testu zamiast oznaczenia sztuki jako NOK. Fail-safe (nigdy
fałszywy PASS), ale operacyjnie błędne i mylące.

Naprawione w 1.1.2. Rozpoznawane statusy: `Pass`, `HI-Limit`/`HI-Lmt`,
`LO-Limit`/`LO-Lmt`, `OFL`, `Abort`, plus warianty spotykane w serii
(`Breakdown`, `Short`, `Arc`, `Ramp-Fail`). `Abort` mapuje się na ABORTED,
nie na FAIL — przerwany test to nie zła sztuka.

---

## 3. POTWIERDZONE: kolejność pól Ground Bond

Zamknięte definitywnie, dwa niezależne dowody:

**Manual, str. 19** — wyświetlacz przy błędzie GND:
```
1-1 HI-Lmt   1.0s
30.0A GND  150mΩ      <- prąd PRZED rezystancją
```
`RD <step>?` zwraca `{memory-step, test type, status, meter 1, meter 2, meter 3}`.

**Wasz log** — przy zadanych 25,0 A:
```
gnd_current = 24,90    gnd_resistance = 73,00 / 95,00 / 106,00
```
24,90 ≈ 25,0 A zadane → to jest meter 1, czyli **prąd jest pierwszy**.

Komentarz w `hipot_controller.py` był poprawny, komentarz
w `ground_bond_test.py` był **błędny**. `gnd_field_order` ustawione na sztywno
`"current_first"` (tryb `"auto"` pozostaje jako opcja awaryjna).

---

## 4. NAJWAŻNIEJSZE USTALENIE PROCESOWE: Ground Bond dryfuje

Ta sama sztuka `079115Z81840226AE`, cztery przebiegi w ciągu 2 minut:

| Godzina | Rezystancja GND | Zmiana | Wynik |
|---|---|---|---|
| 13:31:28 | 95 mΩ | — | Pass |
| 13:32:13 | 98 mΩ | +3 | Pass |
| 13:32:52 | 98 mΩ | +0 | Pass |
| 13:33:25 | **106 mΩ** | **+8** | **HI-Limit → FAIL** |

Limit: 100 mΩ. Zapas w pierwszym przebiegu: **5 mΩ, czyli 5 %**.

W tym samym czasie HiPot na tej samej sztuce był **idealnie powtarzalny**:
1,19 / 1,19 / 1,19 / 1,18 mA — rozrzut 0,01 mA.

Czyli **DUT i tor HiPot są stabilne, dryfuje wyłącznie tor prądowy Ground
Bond**. 11 mΩ narostu w 2 minuty przy 25 A to zachowanie typowe dla
nagrzewającego się albo pogarszającego styku, nie dla wady wyrobu.

Druga sztuka (`082026X50140206AE`, 12:48) dała 73 mΩ — o 22 mΩ mniej.
Taka różnica między egzemplarzami przy tak wąskim zapasie też wskazuje na
udział oprzyrządowania w pomiarze.

### Offset GND = 0

W profilu `1_5KV_GND` offset jest ustawiony na 0. Manual (str. 14) opisuje
tę nastawę jako:

```
Offset =   24mΩ
TEST to Auto Set        (zakres 0 - 100 mΩ)
```

Funkcja offsetu służy dokładnie do wyzerowania rezystancji przewodów
i oprzyrządowania, żeby pomiar dotyczył samego połączenia w DUT. Przy
offsecie 0 do wyniku wchodzi wszystko: kabel prądowy, styki przekaźnika
ESP, oprzyrządowanie.

**Co proponuję sprawdzić na stanowisku (kolejność ma znaczenie):**

1. **Pomiar toru bez DUT** — zewrzyj kable pomiarowe i uruchom GND.
   Odczyt = rezystancja własna toru. Jeśli wyjdzie 20-40 mΩ, masz odpowiedź,
   skąd biorą się 95-106 mΩ.
2. **Pomiar z pominięciem przekaźnika** — jeśli da się tymczasowo podłączyć
   PE bezpośrednio, porównaj odczyt. 25 A przez styki przekaźnika to duże
   obciążenie; różnica powie, ile wnosi przekaźnik.
3. **Powtórzenie serii po ostygnięciu** — jeśli po 15 minutach przerwy
   pierwszy odczyt wraca do ~95 mΩ, potwierdza to nagrzewanie.

**Czego NIE rozstrzygam:** czy w Waszym procesie wolno kompensować offsetem
i jaka wartość limitu wynika ze specyfikacji OEM. To decyzja właściciela
procesu / jakości, nie techniczna — offset zmienia to, co faktycznie mierzycie.
Ustaw go dopiero po potwierdzeniu z osobą odpowiedzialną za specyfikację
testu i zapisz uzasadnienie; zmiana profilu trafia teraz do
`logs/config_audit.log`.

Do tego czasu 1.1.2 **ostrzega o wynikach blisko limitu**: PASS z zapasem
≤ 10 % pokazuje na ekranie `✔ GND PASS (blisko limitu)` na pomarańczowo
i zapisuje w logu procent zapasu. Przy 95 mΩ / 100 mΩ operator zobaczy to
zanim następna sztuka wyjdzie FAIL.

---

## 5. Potwierdzenie starego błędu: GND po nieudanym HiPot

Wiersz 13:12, SN `079115Z81840226AE`:

```
hipot_result = OFL   voltage = 0,63 kV   current = >20.0 mA   time = 0,5 s
gnd_result   = Unknown
error_desc   = NAK na RD 2? i RD 1? — brak wyniku Ground Bond
```

Wg manuala `OFL` z odczytem napięcia to **przeskok / przebicie** w DUT
(przy braku odczytu napięcia byłoby to zwarcie). Stara wersja mimo tego
**przeszła do Ground Bond** — status `OFL` nie łapał się w warunku
`status in ("fail", "error")`, bo parser zapisywał `"done"`.

Ground Bond nie zwrócił wtedy wyniku i nie mógł: po awarii Slaughter
zatrzaskuje stan błędu i wymaga `RESET`, zanim wykona kolejny test
(manual, przypis pod tabelą Failure Mode Displays). Stąd NAK na obu slotach.

W 1.1.0+ ten scenariusz jest zablokowany — do Ground Bond wchodzi się
wyłącznie po jawnym `Pass` HiPot. 1.1.2 dodatkowo opisze to operatorowi jako
„OFL — przeskok/przebicie w DUT przy 0.63 kV".

---

## 6. Wzorzec do przeglądu przez jakość

```
079803Z83440397AE:  09:43 FAIL (HI-Limit 2,48 mA)  ->  09:47 PASS (2,02 mA)
079115Z81840226AE:  13:12 FAIL (OFL, przebicie)    ->  13:31 PASS
                    13:32 PASS -> 13:32 PASS       ->  13:33 FAIL (GND 106 mΩ)
```

Obie sztuki po wyniku FAIL zostały przetestowane ponownie i przeszły. Nie
oceniam tego — nie znam Waszej instrukcji dotyczącej powtórnych testów.
Zwracam uwagę, bo:

- w pierwszym przypadku FAIL i PASS różnią się o 0,46 mA przy limicie
  najwyraźniej ~2,4 mA — to też wynik na granicy,
- w drugim OFL to zgłoszone **przebicie**, a sztuka 19 minut później
  przeszła bez zastrzeżeń. Przebicie, które się nie powtarza, warto
  wyjaśnić — może pochodzić z oprzyrządowania, nie z wyrobu.

Mogę dodać automatyczne wykrywanie powtórnego testu tego samego SN po FAIL
(ostrzeżenie na ekranie + kolumna w CSV) — powiedz, czy chcesz. Nie dodałem
sam, bo to zmiana w przebiegu pracy operatora, nie poprawka błędu.

---

## 7. Zakresy z manuala wprowadzone do walidacji

Panel Inżynieryjny sprawdza teraz nastawy wg specyfikacji 4320, żeby tester
nie odrzucał ich NAK-iem (operator widziałby tylko „brak ACK na EH"):

| Parametr | Zakres 4320 | Źródło |
|---|---|---|
| Napięcie ACW | 0,00 – 5,00 kV AC | spec, str. 33 |
| HI/LO limit ACW | 0,00 – 20,00 mA AC | spec, str. 33 |
| Prąd GND | 3,0 – 30,0 A AC | komenda `EC`, str. 44 |
| Limit GND | 510 mΩ (3-10 A) / **200 mΩ (10,1-25 A)** / 150 mΩ (25,1-30 A) | spec, str. 34 |
| Offset GND | 0 – 100 mΩ | str. 14 |
| Dwell | 0,5 – 999,9 s | str. 34 |

Wasz profil (25,0 A, limit 100 mΩ) mieści się w zakresie 200 mΩ — jest OK.
Doszła walidacja krzyżowa: limit rezystancji jest sprawdzany **względem
nastawionego prądu**, bo dopuszczalne maksimum zależy od zakresu prądowego.

Doszło też rozpoznanie sytuacji „odczyt równy granicy zakresu": jeśli przy
25 A tester zwróci dokładnie 200 mΩ, to nie jest pomiar, tylko „poza
zakresem" — 1.1.2 opisze to wprost, zamiast pokazywać 200 mΩ jako wartość.
