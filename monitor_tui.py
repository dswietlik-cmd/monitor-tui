import os
import re
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright


TUI_URL = (
    "https://www.tui.pl/wypoczynek/cypr/larnaka/"
    "marlita-hotel-apartments-lca15000/OfferCodeWS/"
    "GDNLCA20260905025520260905202609120815L07LCA15000"
    "STX1AA02ROASTX1A02FCYY"
)

PRICE_FILE = Path("ostatnia_cena.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

# Zakres ochronny przed przypadkowym odczytaniem np. raty albo rabatu.
MIN_PRICE = 1000
MAX_PRICE = 50000


def normalize_price(value: str) -> float:
    value = value.replace("\xa0", " ").strip()
    value = value.replace(" ", "").replace(".", "").replace(",", ".")
    return float(value)


def extract_prices(text: str):
    """
    Zwraca kandydatów cenowych znalezionych na stronie.
    Każdy element: (cena, kontekst).
    """
    pattern = re.compile(
        r"(?<!\d)(\d{1,3}(?:[ .\u00a0]\d{3})+(?:,\d{2})?"
        r"|\d{4,6}(?:,\d{2})?)\s*zł",
        re.IGNORECASE,
    )

    candidates = []

    for match in pattern.finditer(text):
        try:
            price = normalize_price(match.group(1))
        except ValueError:
            continue

        if not (MIN_PRICE <= price <= MAX_PRICE):
            continue

        start = max(0, match.start() - 120)
        end = min(len(text), match.end() + 120)
        context = " ".join(text[start:end].split())

        candidates.append((price, context))

    return candidates


def choose_offer_price(text: str) -> float:
    candidates = extract_prices(text)

    if not candidates:
        raise RuntimeError("Nie znaleziono żadnej sensownej ceny w treści strony.")

    # Preferujemy cenę znajdującą się w pobliżu typowych etykiet ceny końcowej.
    strong_keywords = [
        "cena za wszystkich",
        "cena całkowita",
        "cena calkowita",
        "łączna cena",
        "laczna cena",
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
    ]

    scored = []

    for price, context in candidates:
        c = context.lower()
        score = 0

        if any(k in c for k in strong_keywords):
            score += 10

        if any(k in c for k in weak_keywords):
            score += 2

        if any(k in c for k in negative_keywords):
            score -= 5

        scored.append((score, price, context))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    print("Kandydaci cenowi:")
    for score, price, context in scored[:10]:
        print(f"  score={score:>2}  cena={price:.2f} zł  kontekst={context[:220]}")

    best_score, best_price, _ = scored[0]

    # Jeśli nie znaleźliśmy silnego kontekstu, wybieramy najwyższą
    # sensowną cenę. Dla ceny całej wycieczki jest to bezpieczniejsze
    # niż wybieranie najniższej kwoty ze strony.
    if best_score <= 0:
        best_price = max(price for price, _ in candidates)

    return float(best_price)


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
                name=re.compile(re.escape(label), re.IGNORECASE),
            )
            if button.count() > 0:
                button.first.click(timeout=2500)
                page.wait_for_timeout(1500)
                return
        except Exception:
            pass


def get_price() -> float:
    print("Otwieram TUI...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        page = browser.new_page(
            viewport={"width": 1920, "height": 1080},
            locale="pl-PL",
        )

        try:
            page.goto(
                TUI_URL,
                wait_until="domcontentloaded",
                timeout=120_000,
            )

            accept_cookies(page)

            # TUI pobiera ofertę dynamicznie.
            # Czekamy maksymalnie ok. 35 sekund na pojawienie się kwoty.
            for _ in range(7):
                page.wait_for_timeout(5000)
                body_text = page.locator("body").inner_text()

                if re.search(r"\d[\d .\u00a0]*\s*zł", body_text, re.IGNORECASE):
                    break

            body_text = page.locator("body").inner_text()

            Path("ostatnia_strona.txt").write_text(
                body_text,
                encoding="utf-8",
            )

            page.screenshot(
                path="ostatni_zrzut.png",
                full_page=True,
            )

            price = choose_offer_price(body_text)
            print(f"Wybrana cena: {price:.2f} zł")
            return price

        finally:
            browser.close()


def load_previous_price():
    if not PRICE_FILE.exists():
        return None

    try:
        data = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
        return float(data["price"])
    except Exception:
        return None


def save_price(price: float):
    data = {
        "price": price,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": TUI_URL,
    }

    PRICE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_ntfy(old_price: float, new_price: float):
    if not NTFY_TOPIC:
        raise RuntimeError(
            "Brak NTFY_TOPIC. Dodaj sekret NTFY_TOPIC w GitHub Actions."
        )

    difference = new_price - old_price

    if difference < 0:
        title = "TUI - CENA SPADŁA"
        body = (
            f"Marlita Hotel Apartments\n"
            f"Było: {old_price:.0f} zł\n"
            f"Jest: {new_price:.0f} zł\n"
            f"Spadek: {abs(difference):.0f} zł"
        )
        priority = "high"
        tags = "moneybag,chart_with_downwards_trend"
    else:
        title = "TUI - CENA WZROSŁA"
        body = (
            f"Marlita Hotel Apartments\n"
            f"Było: {old_price:.0f} zł\n"
            f"Jest: {new_price:.0f} zł\n"
            f"Wzrost: {difference:.0f} zł"
        )
        priority = "default"
        tags = "warning,chart_with_upwards_trend"

    response = requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Click": TUI_URL,
        },
        timeout=30,
    )
    response.raise_for_status()
    print("Powiadomienie ntfy wysłane.")


def main():
    old_price = load_previous_price()
    new_price = get_price()

    if old_price is None:
        print("Pierwszy pomiar. Zapisuję cenę bez wysyłania powiadomienia.")
        save_price(new_price)
        return

    print(f"Poprzednia cena: {old_price:.2f} zł")
    print(f"Nowa cena:       {new_price:.2f} zł")

    if new_price == old_price:
        print("Cena bez zmian. Nic nie wysyłam.")
        return

    print("Cena się zmieniła.")
    send_ntfy(old_price, new_price)
    save_price(new_price)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        raise
