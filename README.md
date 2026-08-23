# Monitor ceny TUI + ntfy + GitHub Actions

Program sprawdza cenę konkretnej oferty TUI raz na godzinę.
Powiadomienie ntfy jest wysyłane tylko wtedy, gdy cena się zmieni.

## Pliki

- `monitor_tui.py` — jednorazowe sprawdzenie ceny.
- `.github/workflows/monitor.yml` — uruchamianie w GitHub Actions.
- `requirements.txt` — zależności Pythona.
- `ostatnia_cena.json` — pojawi się automatycznie po pierwszym poprawnym uruchomieniu.

## Konfiguracja

1. Utwórz repozytorium na GitHubie.
2. Wgraj wszystkie pliki z tego pakietu.
3. Wejdź w:
   Settings -> Secrets and variables -> Actions -> New repository secret
4. Nazwa sekretu:
   NTFY_TOPIC
5. Wartość:
   dokładnie nazwa tematu subskrybowanego w aplikacji ntfy na iPhonie.
6. Wejdź w zakładkę Actions i uruchom workflow ręcznie przez "Run workflow".
7. Pierwsze uruchomienie tylko zapamiętuje cenę.
8. Kolejne uruchomienia wysyłają ntfy wyłącznie przy zmianie ceny.

Workflow uruchamia się automatycznie o 17. minucie każdej godziny.
