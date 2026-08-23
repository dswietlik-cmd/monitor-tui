import os
import re
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright


# ============================================================
# KONFIGURACJA
# ============================================================

TUI_URL = (
    "https://www.tui.pl/wypoczynek/cypr/larnaka/"
    "marlita-hotel-apartments-lca15000/OfferCodeWS/"
    "GDNLCA20260905025520260905202609120815L07LCA15000"
    "STX1AA02ROASTX1A02FCYY"
)

PRICE_FILE = Path("ostatnia_cena.json")

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

MIN_PRICE = 1000
MAX_PRICE = 50000


# ============================================================
# NORMALIZACJA CENY
# ============================================================

def normalize_price(value: str) -> float:
    value = value.replace("\xa0", " ").strip()
    value = (
        value
        .replace(" ", "")
        .replace(".", "")
        .replace(",", ".")
    )

    return float(value)


# ============================================================
# WYSZUKIWANIE CEN
# ============================================================

def extract_prices(text: str):
    pattern = re.compile(
        r"(?<!\d)"
        r"(\d{1,3}(?:[ .\u00a0]\d{3})+(?:,\d{2})?"
        r"|\d{4,6}(?:,\d{2})?)"
        r"\s*zł",
        re.IGNORECASE,
    )

    candidates = []

    for match in pattern.finditer(text):

        try:
            price = normalize_price(
                match.group(1)
            )

        except ValueError:
            continue

        if not (
            MIN_PRICE
            <= price
            <= MAX_PRICE
        ):
            continue

        start = max(
            0,
            match.start() - 120
        )

        end = min(
            len(text),
            match.end() + 120
        )

        context = " ".join(
            text[start:end].split()
        )

        candidates.append(
            (price, context)
        )

    return candidates


# ============================================================
# WYBÓR WŁAŚCIWEJ CENY
# ============================================================

def choose_offer_price(text: str) -> float:

    # --------------------------------------------------------
    # Najpierw szukamy dokładnie "Cena razem"
    # --------------------------------------------------------

    match = re.search(
        r"Cena\s+razem:\s*"
        r"(\d{1,3}(?:[ .\u00a0]\d{3})+(?:,\d{2})?"
        r"|\d{4,6}(?:,\d{2})?)"
        r"\s*zł",
        text,
        re.IGNORECASE
    )

    if match:

        price = normalize_price(
            match.group(1)
        )

        print(
            f"Znaleziono 'Cena razem': "
            f"{price:.2f} zł"
        )

        return price

    # --------------------------------------------------------
    # Awaryjny mechanizm
    # --------------------------------------------------------

    candidates = extract_prices(text)

    if not candidates:

        raise RuntimeError(
            "Nie znaleziono żadnej sensownej ceny "
            "w treści strony."
        )

    strong_keywords = [
        "cena za wszystkich",
        "cena całkowita",
        "cena calkowita",
        "łączna cena",
        "laczna cena",
        "cena razem",
        "razem",
        "do zapłaty",
        "do zaplaty",
    ]

    weak_keywords = [
        "cena",
        "rezerwuj",
        "wybierz",
    ]

    negative_keywords = [
        "rata",
        "miesięcznie",
        "miesiecznie",
        "zniżka",
        "znizka",
        "oszczędzasz",
        "oszczedzasz",
        "od osoby",
        "za osobę",
        "za osobe",
        "ubezpieczeniem",
    ]

    scored = []

    for price, context in candidates:

        c = context.lower()

        score = 0

        if any(
            keyword in c
            for keyword in strong_keywords
        ):
            score += 10

        if any(
            keyword in c
            for keyword in weak_keywords
        ):
            score += 2

        if any(
            keyword in c
            for keyword in negative_keywords
        ):
            score -= 5

        scored.append(
            (
                score,
                price,
                context
            )
        )

    scored.sort(
        key=lambda x: (
            x[0],
            x[1]
        ),
        reverse=True
    )

    print(
        "Kandydaci cenowi:"
    )

    for score, price, context in scored[:10]:

        print(
            f"score={score:>2} "
            f"cena={price:.2f} zł "
            f"kontekst={context[:220]}"
        )

    return float(
        scored[0][1]
    )


# ============================================================
# COOKIES
# ============================================================

def accept_cookies(page):

    labels = [
        "Akceptuję",
        "Akceptuj",
        "Zaakceptuj wszystkie",
        "Zgadzam się",
        "Accept",
        "Accept all",
    ]

    for label in labels:

        try:

            button = page.get_by_role(
                "button",
                name=re.compile(
                    re.escape(label),
                    re.IGNORECASE
                ),
            )

            if button.count() > 0:

                button.first.click(
                    timeout=2500
                )

                page.wait_for_timeout(
                    1500
                )

                return

        except Exception:
            pass


# ============================================================
# POBIERANIE CENY TUI
# ============================================================

def get_price() -> float:

    print(
        "Otwieram TUI..."
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ],
        )

        page = browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            },
            locale="pl-PL",
        )

        try:

            page.goto(
                TUI_URL,
                wait_until="domcontentloaded",
                timeout=120_000,
            )

            accept_cookies(
                page
            )

            print(
                "Czekam na załadowanie ceny..."
            )

            body_text = ""

            # maksymalnie około 35 sekund
            for _ in range(7):

                page.wait_for_timeout(
                    5000
                )

                body_text = (
                    page.locator("body")
                    .inner_text()
                )

                if re.search(
                    r"\d[\d .\u00a0]*\s*zł",
                    body_text,
                    re.IGNORECASE
                ):
                    break

            body_text = (
                page.locator("body")
                .inner_text()
            )

            # diagnostyka
            Path(
                "ostatnia_strona.txt"
            ).write_text(
                body_text,
                encoding="utf-8",
            )

            page.screenshot(
                path="ostatni_zrzut.png",
                full_page=True,
            )

            price = choose_offer_price(
                body_text
            )

            print(
                f"Wybrana cena: "
                f"{price:.2f} zł"
            )

            return price

        finally:

            browser.close()


# ============================================================
# ODCZYT POPRZEDNIEJ CENY
# ============================================================

def load_previous_price():

    if not PRICE_FILE.exists():
        return None

    try:

        data = json.loads(
            PRICE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return float(
            data["price"]
        )

    except Exception:
        return None


# ============================================================
# ZAPIS NOWEJ CENY
# ============================================================

def save_price(
    price: float
):

    data = {
        "price": price,
        "checked_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
        "url": TUI_URL,
    }

    PRICE_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8",
    )


# ============================================================
# POWIADOMIENIE NTFY
# ============================================================

def send_ntfy(
    old_price: float,
    new_price: float
):

    if not NTFY_TOPIC:

        raise RuntimeError(
            "Brak NTFY_TOPIC. "
            "Dodaj sekret NTFY_TOPIC "
            "w GitHub Actions."
        )

    difference = (
        new_price - old_price
    )

    # --------------------------------------------------------
    # CENA SPADŁA
    # --------------------------------------------------------

    if difference < 0:

        title = (
            "TUI - CENA SPADŁA"
        )

        message = (
            "Marlita Hotel Apartments\n"
            f"Było: {old_price:.0f} zł\n"
            f"Jest: {new_price:.0f} zł\n"
            f"Spadek: "
            f"{abs(difference):.0f} zł"
        )

        priority = 4

        tags = [
            "moneybag",
            "chart_with_downwards_trend"
        ]

    # --------------------------------------------------------
    # CENA WZROSŁA
    # --------------------------------------------------------

    else:

        title = (
            "TUI - CENA WZROSŁA"
        )

        message = (
            "Marlita Hotel Apartments\n"
            f"Było: {old_price:.0f} zł\n"
            f"Jest: {new_price:.0f} zł\n"
            f"Wzrost: "
            f"{difference:.0f} zł"
        )

        priority = 3

        tags = [
            "warning",
            "chart_with_upwards_trend"
        ]

    data = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": tags,
        "click": TUI_URL
    }

    response = requests.post(
        "https://ntfy.sh",
        json=data,
        timeout=30
    )

    response.raise_for_status()

    print(
        "Powiadomienie ntfy wysłane."
    )


# ============================================================
# PROGRAM GŁÓWNY
# ============================================================

def main():

    old_price = (
        load_previous_price()
    )

    new_price = (
        get_price()
    )

    # --------------------------------------------------------
    # PIERWSZE URUCHOMIENIE
    # --------------------------------------------------------

    if old_price is None:

        print(
            "Pierwszy pomiar."
        )

        print(
            "Zapisuję cenę bez wysyłania "
            "powiadomienia."
        )

        save_price(
            new_price
        )

        return

    print(
        f"Poprzednia cena: "
        f"{old_price:.2f} zł"
    )

    print(
        f"Nowa cena:       "
        f"{new_price:.2f} zł"
    )

    # --------------------------------------------------------
    # BRAK ZMIANY
    # --------------------------------------------------------

    if new_price == old_price:

        print(
            "Cena bez zmian."
        )

        print(
            "Nic nie wysyłam."
        )

        return

    # --------------------------------------------------------
    # CENA SIĘ ZMIENIŁA
    # --------------------------------------------------------

    print(
        "Cena się zmieniła."
    )

    send_ntfy(
        old_price,
        new_price
    )

    save_price(
        new_price
    )

    print(
        "Nowa cena została zapisana."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        print(
            f"BŁĄD: {exc}",
            file=sys.stderr
        )

        raise
