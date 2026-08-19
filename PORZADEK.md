# Co w katalogu projektu jest potrzebne

Odpowiedź na pytanie „czy wszystko jest w folderze potrzebne".
Skrót: **komplet aplikacji jest, build się uda.** Reszta poniżej.

Zamiast czytać tę tabelę za każdym razem — uruchom:

```
python sprawdz_projekt.py
```

Sprawdza obecność plików, **spójność wersji**, pułapki w `config.json`
i rzeczy wrażliwe. Nic nie zmienia, tylko raportuje.

---

## 1. Potrzebne — pakowane do EXE (13 plików)

Te muszą być, inaczej `create_exe.py` przerwie build:

```
main.py              config.py            verdict.py
app_logging.py       runtime_state.py     login_screen.py
main_screen.py       hipot_controller.py  relay_controller.py
result_logger.py     engineer_panel.py    password_dialog.py
ted_client.py
```

Na Twoim zrzucie ekranu **wszystkie 13 są obecne.**

## 2. Potrzebne — obok EXE / do buildu

| Plik | Rola |
|---|---|
| `create_exe.py` | builder |
| `config.json` | konfiguracja stanowiska, leży obok EXE, edytowalna |
| `config.example.json` | wzorzec bez danych osobowych — do repo |
| `set_engineer_password.py` | ustawienie hasła bez uruchamiania aplikacji |
| `sprawdz_projekt.py` | kontrola katalogu przed buildem |

## 3. Potrzebne — dokumentacja

`WDROZENIE.md`, `DIAGNOSTYKA.md`, `ANALIZA_LOGU_20260817.md`, `PORZADEK.md`

## 4. Diagnostyka — zostaje w repo, NIE idzie do EXE

| Plik | Status |
|---|---|
| `ground_bond_test.py` | **podmieniony** — miał błędny opis kolejności pól, doszedł tryb `--zero` do pomiaru rezystancji toru |
| `relay_test.py` | przydatny, ma `--dry-run` bez sprzętu |
| `test_ted_send.py` | przydatny do testów TED z IT |
| `generate_doc_screenshots.py` | zostaw, jeśli używasz do dokumentacji |

`ground_bond_test.py --zero` to teraz najszybsza droga do rozstrzygnięcia
sprawy z 95-106 mΩ: zwierasz kable bez DUT i widzisz, ile wnosi sam tor.

## 5. Do archiwum albo do kosza

| Plik | Dlaczego |
|---|---|
| `niedzialajce3rzeczy.py` | 850 B, nazwa mówi sama za siebie |
| `bezRTS.py` | eksperyment z RS-232, jeśli nieużywany |
| `hipot_test_connection.py` | zastąpiony zakładką **Diagnostyka** w panelu; ma `PORT = 'COM10'` na sztywno i wysyła `\r` bez `\n` |
| `replacements.txt` | 23 B, sprawdź czy jeszcze coś robi |
| `hipot_bose/` (podfolder) | **kopia projektu z 13.04** — najbardziej mylące, łatwo edytować zły plik |

`logger.py` już go nie ma — dobrze, to był martwy kod.

Proponuję `_archiwum/` w katalogu projektu i `_archiwum/` w `.gitignore`
(już dodane). Firmware (`arduino`, `arduino2.txt`) warto przenieść do
`firmware/` — to nie jest kod aplikacji, ale jest istotny i powinien zostać.

## 6. Generowane — do `.gitignore`, można kasować

`dist/`, `build/`, `__pycache__/`, `.idea/`, `.venv/`

`logs/` w katalogu źródłowym powstaje, gdy uruchamiasz z PyCharma.
Wyniki produkcyjne są w **`dist/HiPot Bose/logs/`** — to dwa różne miejsca,
łatwo szukać w złym.

---

## 7. Dwie rzeczy do sprawdzenia od razu

### `.env` z kluczem TED

Leży w katalogu projektu. Dołączony `.gitignore` go wyklucza, ale **wpis
w .gitignore nie usuwa pliku z historii repo.** Sprawdź:

```
git log --all --oneline -- .env
```

Jeśli cokolwiek zwróci — klucz był w repo i trzeba go uznać za ujawniony.
Poproś IT o rotację klucza funkcji. Nie wklejaj tu jego treści.

### Spójność wersji

Daty na Twoim zrzucie wskazują, że `ted_client.py` jest z **18.08 08:24**,
a pozostałe pliki z **17.08 14:40-14:49**. To jest **poprawne** — paczka 1.1.3
różniła się od 1.1.2 wyłącznie w `ted_client.py`. `sprawdz_projekt.py`
potwierdzi to jednoznacznie, sprawdzając znaczniki poprawek w każdym pliku,
a nie daty.

To najgroźniejszy rodzaj pomyłki w tym projekcie: przy pomieszanych wersjach
aplikacja **wstaje i wygląda normalnie**, a błąd wychodzi dopiero na wyniku.
Dlatego kontrola jest po zawartości plików, nie po datach.

---

## 8. Kolejność przy następnym wdrożeniu

```
1. python sprawdz_projekt.py     <- musi być bez BŁĘDÓW
2. python create_exe.py
3. skopiuj cały folder dist/HiPot Bose na stanowisko
4. pierwsze uruchomienie: zmień hasło (Panel → Bezpieczeństwo)
5. Diagnostyka → Test połączenia → wpisz tokeny SA? do config.json
```

`create_exe.py` domyślnie działa w trybie ścisłym — nie przyjmie pliku
`main(2).py`. To celowe: chroni przed zbudowaniem EXE z przypadkowej wersji.
