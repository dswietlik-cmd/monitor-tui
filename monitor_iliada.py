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
    "iliada-beach-hotel-lca15034/OfferCodeWS/"
    "GDNLCA20260905025520260905202609120815L07LCA15034"
    "DZM1AA02ROADZM1A02FCYY"
)

HOTEL_NAME = "Iliada Beach Hotel"
PRICE_FILE = Path("ostatnia_cena_iliada.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

def normalize_price(value: str) -> float:
    return float(value.replace("\xa0", " ").strip().replace(" ", "").replace(".", "").replace(",", "."))

def choose_offer_price(text: str) -> float:
    match = re.search(
        r"Cena\s+razem:\s*(\d{1,3}(?:[ .\u00a0]\d{3})+(?:,\d{2})?|\d{4,6}(?:,\d{2})?)\s*zł",
        text,
        re.IGNORECASE,
    )
    if match:
        price = normalize_price(match.group(1))
        print(f"Znaleziono 'Cena razem': {price:.2f} zł")
        return price

    pattern = re.compile(
        r"(?<!\d)(\d{1,3}(?:[ .\u00a0]\d{3})+(?:,\d{2})?|\d{4,6}(?:,\d{2})?)\s*zł",
        re.IGNORECASE,
    )
    candidates = []
    for m in pattern.finditer(text):
        try:
            price = normalize_price(m.group(1))
        except ValueError:
            continue
        if 1000 <= price <= 50000:
            start = max(0, m.start() - 120)
            end = min(len(text), m.end() + 120)
            context = " ".join(text[start:end].split()).lower()
            score = 0
            if "razem" in context or "cena całkowita" in context or "cena calkowita" in context:
                score += 10
            if "cena" in context:
                score += 2
            if any(k in context for k in ["za osobę", "za osobe", "od osoby", "ubezpieczeniem", "rata"]):
                score -= 5
            candidates.append((score, price))

    if not candidates:
        raise RuntimeError("Nie znaleziono ceny na stronie TUI.")

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return float(candidates[0][1])

def accept_cookies(page):
    for label in ["Akceptuję", "Akceptuj", "Zaakceptuj wszystkie", "Zgadzam się", "Accept", "Accept all"]:
        try:
            btn = page.get_by_role("button", name=re.compile(re.escape(label), re.IGNORECASE))
            if btn.count() > 0:
                btn.first.click(timeout=2500)
                page.wait_for_timeout(1500)
                return
        except Exception:
            pass

def get_price() -> float:
    print(f"Otwieram TUI - {HOTEL_NAME}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, locale="pl-PL")
        try:
            page.goto(TUI_URL, wait_until="domcontentloaded", timeout=120000)
            accept_cookies(page)
            for _ in range(7):
                page.wait_for_timeout(5000)
                body = page.locator("body").inner_text()
                if re.search(r"\d[\d .\u00a0]*\s*zł", body, re.IGNORECASE):
                    break
            body = page.locator("body").inner_text()
            Path("ostatnia_strona_iliada.txt").write_text(body, encoding="utf-8")
            page.screenshot(path="ostatni_zrzut_iliada.png", full_page=True)
            price = choose_offer_price(body)
            print(f"Wybrana cena: {price:.2f} zł")
            return price
        finally:
            browser.close()

def load_previous_price():
    if not PRICE_FILE.exists():
        return None
    try:
        return float(json.loads(PRICE_FILE.read_text(encoding="utf-8"))["price"])
    except Exception:
        return None

def save_price(price: float):
    data = {
        "hotel": HOTEL_NAME,
        "price": price,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": TUI_URL,
    }
    PRICE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def send_ntfy(old_price: float, new_price: float):
    if not NTFY_TOPIC:
        raise RuntimeError("Brak NTFY_TOPIC w GitHub Actions.")

    diff = new_price - old_price
    if diff < 0:
        title = "TUI - CENA SPADŁA"
        message = (
            f"{HOTEL_NAME}\n"
            f"Było: {old_price:.0f} zł\n"
            f"Jest: {new_price:.0f} zł\n"
            f"Spadek: {abs(diff):.0f} zł"
        )
        priority = 4
        tags = ["moneybag", "chart_with_downwards_trend"]
    else:
        title = "TUI - CENA WZROSŁA"
        message = (
            f"{HOTEL_NAME}\n"
            f"Było: {old_price:.0f} zł\n"
            f"Jest: {new_price:.0f} zł\n"
            f"Wzrost: {diff:.0f} zł"
        )
        priority = 3
        tags = ["warning", "chart_with_upwards_trend"]

    response = requests.post(
        "https://ntfy.sh",
        json={
            "topic": NTFY_TOPIC,
            "title": title,
            "message": message,
            "priority": priority,
            "tags": tags,
            "click": TUI_URL,
        },
        timeout=30,
    )
    response.raise_for_status()
    print("Powiadomienie ntfy wysłane.")

def main():
    old_price = load_previous_price()
    new_price = get_price()

    if old_price is None:
        print("Pierwszy pomiar - zapisuję cenę bez powiadomienia.")
        save_price(new_price)
        return

    print(f"Poprzednia cena: {old_price:.2f} zł")
    print(f"Nowa cena:       {new_price:.2f} zł")

    if new_price == old_price:
        print("Cena bez zmian.")
        return

    print("Cena się zmieniła.")
    send_ntfy(old_price, new_price)
    save_price(new_price)
    print("Nowa cena została zapisana.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        raise
