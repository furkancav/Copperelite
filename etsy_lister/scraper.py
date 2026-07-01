"""Scrape product data from a Ticimax-based shop (e.g. bamyum.com).

Two entry points:
  - collect_product_urls(category_url, max_pages): gather product links from a category
  - scrape_product(url): extract title, price, description, image URLs

Ticimax markup varies between themes; selectors below use resilient fallbacks
(Open Graph meta + common class names). Adjust SELECTORS if your theme differs.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field, asdict

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


@dataclass
class Product:
    url: str
    title: str = ""
    price_try: float | None = None
    description: str = ""
    image_urls: list[str] = field(default_factory=list)
    sku: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _get(url: str, delay: float = 1.0) -> BeautifulSoup:
    time.sleep(delay)
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def _parse_try_price(text: str) -> float | None:
    # "₺1.337,00" -> 1337.00 ; "1.337,00 TL" -> 1337.00
    m = re.search(r"([\d][\d.\s]*,\d{2}|\d[\d.\s]*)", text.replace("\xa0", " "))
    if not m:
        return None
    raw = m.group(1).strip().replace(" ", "")
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def scrape_product(url: str, delay: float = 1.0) -> Product:
    soup = _get(url, delay)
    p = Product(url=url)

    # Title
    og_t = soup.find("meta", property="og:title")
    h1 = soup.find("h1")
    p.title = (og_t["content"].strip() if og_t and og_t.get("content")
               else (h1.get_text(strip=True) if h1 else ""))

    # Price: look for elements/text containing the currency
    price_text = ""
    for sel in ['[class*="fiyat"]', '[class*="price"]', '#UrunFiyat', '.spanFiyat']:
        el = soup.select_one(sel)
        if el and ("₺" in el.get_text() or "TL" in el.get_text()):
            price_text = el.get_text(" ", strip=True)
            break
    if not price_text:
        m = re.search(r"(₺[\s\d.,]+)", soup.get_text(" ", strip=True))
        price_text = m.group(1) if m else ""
    p.price_try = _parse_try_price(price_text)

    # Description
    desc_el = (soup.select_one('[class*="urunaciklama"]')
               or soup.select_one('#UrunAciklama')
               or soup.find("meta", attrs={"name": "description"}))
    if desc_el is not None:
        p.description = (desc_el.get("content") if desc_el.name == "meta"
                         else desc_el.get_text("\n", strip=True))

    # Images: collect high-res ticimax product image URLs
    imgs = set()
    for img in soup.find_all("img"):
        src = img.get("data-original") or img.get("data-src") or img.get("src") or ""
        if "urunresimleri" in src or "/buyuk/" in src:
            src = src.split("?")[0]
            if src.startswith("//"):
                src = "https:" + src
            imgs.add(src)
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        imgs.add(og_img["content"].split("?")[0])
    # Prefer the "buyuk" (large) variants
    p.image_urls = sorted(imgs, key=lambda u: (0 if "buyuk" in u else 1, u))

    # SKU / stock code if present
    sku_el = soup.select_one('[class*="stokkodu"], [class*="stok-kodu"]')
    if sku_el:
        p.sku = re.sub(r"\D", "", sku_el.get_text()) or sku_el.get_text(strip=True)
    return p


def collect_product_urls(category_url: str, max_pages: int = 50, delay: float = 1.0) -> list[str]:
    """Walk a Ticimax category, following ?sayfa=N pagination, collecting product links.

    Ticimax product URLs are root-level slugs (no /kategori path). We collect anchors
    that look like product detail pages. If your theme loads products via AJAX/infinite
    scroll, export the URLs another way and feed them via a urls.txt file instead.
    """
    found: list[str] = []
    seen = set()
    base = re.match(r"^(https?://[^/]+)", category_url).group(1)
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in category_url else "?"
        url = f"{category_url}{sep}sayfa={page}"
        try:
            soup = _get(url, delay)
        except Exception:
            break
        page_links = []
        for a in soup.select("a[href]"):
            href = a["href"]
            if href.startswith("/"):
                href = base + href
            if not href.startswith(base):
                continue
            # product pages are slugs; skip nav/category/system links
            if re.search(r"/(checkout|sepet|iletisim|hakkimizda|uyelik|login|kategori|"
                         r"avize-sarkit|lambader|aplik|outlet)\b", href):
                continue
            if href.rstrip("/") == base or href.count("/") < 3:
                continue
            if href not in seen:
                seen.add(href)
                page_links.append(href)
        if not page_links:
            break
        found.extend(page_links)
    return found
