# HiPot Bose — jak zebrać dane do zgłoszenia

## Konsoli NIE MA — jest plik logu

EXE jest budowany z `--windowed`, więc **żadna konsola się nie pojawi**, nawet
po podłączeniu laptopa. Wszystko, co dawniej szło w `print()`, leci teraz do:

```
<folder z EXE>\logs\app.log
```

Od wersji 1.1.1 log zawiera **cały ruch RS-232** — każdą komendę do testera
i surową odpowiedź, w tym odpowiedzi na `RD 1?` / `RD 2?`. To jest dokładnie
to, czego brakowało, żeby rozstrzygnąć format wyniku Ground Bond.

Przy zgłoszeniu wyślij:

1. `logs\app.log` (albo fragment obejmujący problematyczny test)
2. `logs\hipot_log_<data>.csv` — wiersz danej sztuki
3. numer SN i przybliżoną godzinę testu

## Jeśli wolisz jednak widzieć konsolę na żywo

```
python create_exe.py --console
```

Build diagnostyczny, identyczny funkcjonalnie, ale z okienkiem konsoli.
Do produkcji wracaj na zwykły `python create_exe.py`.

## Co szukać w logu

### Wynik Ground Bond

```
QUERY_RAW >> RD 2?          | RESP << b'\x15\n'
QUERY_RAW >> RD 1?          | RESP << b'1,2,GND,Pass,25.10,65,1.0\r\n'
Ground Bond: wynik znaleziony w RD 1? (przebieg 1): '1,2,GND,Pass,25.10,65,1.0'
GND parts (7): ['1', '2', 'GND', 'Pass', '25.10', '65', '1.0'] | idx werdyktu=3
```

To pokazuje, **w którym slocie** tester trzyma wynik i **jak wygląda rekord**.
Jeśli w Waszym przypadku wynik faktycznie leży pod `RD 1?`, log to potwierdzi
w pierwszej linijce.

### Kolejność pól prąd / rezystancja

Jeśli w logu pojawi się:

```
GND: nie rozpoznano, które pole to prąd ('65', '25.10') przy zadanym 25.00 A
```

to znaczy, że automat nie dał rady i trzeba ustawić na sztywno w `config.json`:

```json
"hipot": { "gnd_field_order": "resistance_first" }
```

Przy prądzie GND 25 A i rezystancji rzędu dziesiątek mΩ automat zwykle
rozpoznaje poprawnie — sprawdź w logu, czy ostrzeżenie w ogóle występuje.

### Timeout wyniku

```
Ground Bond: brak wyniku w 6.1 s. Odpytane slots: RD 2?, RD 1?.
Odebrano: RD 2?='...'; RD 1?='...'. Wysłano STOP i RESET.
```

Pole `Odebrano` mówi, co tester naprawdę odpowiadał. Jeśli oba slots są puste
albo zwracają NAK, problem jest po stronie testera / sekwencji, nie parsera.

### Watchdog ESP

```
Heartbeat do ESP nieudany: ...
WATCHDOG:TIMEOUT->HIPOT
```

Pierwsza linia = PC nie dowozi PING. Druga (z ESP) = przekaźnik wrócił sam
na HIPOT. Jeśli druga pojawia się **w trakcie** Ground Bond, zgłoś to od razu —
oznacza przełączenie styków pod prądem.

### Odpowiedź testera na `SA?`

Panel Inżynieryjny → Diagnostyka → **Test połączenia (RESET)** wypisuje
odpowiedź na `SA?` razem z podpowiedzią. Wklej znalezione słowa do
`config.json`:

```json
"hipot": {
    "status_busy_tokens": ["TESTING"],
    "status_idle_tokens": ["READY"]
}
```

Dopóki listy są puste, aplikacja opiera się wyłącznie na pollingu `RD n?` —
działa, ale bez drugiego potwierdzenia stanu testera.

## Objętość logu

Rotacja 5 MB × 10 plików, ~40 linii na test. Przy 300 testach na zmianę plik
rośnie o kilkaset kB dziennie — bezpiecznie. Log audytowy
(`logs\config_audit.log`) jest osobny i nie jest kasowany przez rotację
diagnostyki.
