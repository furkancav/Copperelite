# Etsy Bulk Lister

Tedarikçi sitesinden (bamyum / Ticimax) ürünleri çekip, GPT/Gemini ile İngilizce SEO içeriği üretip, **Etsy API v3** üzerinden **taslak ilanlar** oluşturan komut satırı aracı. Görselleri (tedarikçi + opsiyonel AI lifestyle) indirip ilana yükler. Resume edilebilir, hız-sınırlı, hataları loglar.

> Bu program **senin bilgisayarında** çalışır (internet + dosya erişimi orada var). Etsy'ye doğrudan API ile bağlanır — tarayıcı sürmek yok.

---

## 1. Kurulum

```bash
cd etsy-bulk-lister
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # anahtarları doldur
cp config.yaml.example config.yaml
```

## 2. Etsy API anahtarı (tek seferlik)

1. https://www.etsy.com/developers/your-apps → **Create a New App**.
2. App onaylanınca **Keystring** (API key) ve **Shared Secret**'i `.env`'e yaz.
3. App ayarlarında **Callback URL** olarak `.env`'deki `ETSY_REDIRECT_URI` ile **birebir aynı** değeri ekle (`http://localhost:3003/callback`).
4. Yetkilendir:

```bash
python run.py auth     # tarayıcı açılır, Etsy'de izin ver
```

Token `data/etsy_token.json`'a kaydedilir ve otomatik yenilenir.

## 3. Mağaza bilgilerini al

```bash
python run.py shopinfo
```

Çıktıdan `shop_id`, `shipping_profile_id`, `return_policy_id`, `shop_section_id` değerlerini al ve `config.yaml`'a yaz. (Kargo profili yoksa Etsy panelinden bir tane oluştur — fiziksel ürün için zorunlu.)

## 4. Kategori (taxonomy) ID'si bul

```bash
python run.py taxonomy --search "pendant"
```

Doğru `taxonomy_id`'yi `config.yaml`'a yaz (örn. Pendant Lights).

## 5. Ürün linklerini topla

```bash
# Kategori sayfasını gez:
python run.py crawl --category "https://www.bamyum.com/avize-sarkit"
# veya elindeki linkleri dosyadan ver (her satıra 1 URL):
python run.py crawl --urls-file urls.txt
```

> Ticimax temaları ürünleri bazen AJAX ile yükler; kategori gezme eksik kalırsa `urls.txt` yöntemini kullan. `scraper.py` içindeki seçiciler tema değişirse ayarlanmalı.

## 6. ÖNCE 1 ürünle test et

```bash
python run.py run --shop-id <SHOP_ID> --limit 1 --dry-run   # hiç yazmadan dener
python run.py run --shop-id <SHOP_ID> --limit 1             # 1 gerçek taslak oluşturur
```

Etsy panelinde **Drafts** altında ilanı kontrol et. İçerik/fiyat/görsel iyiyse devam:

```bash
python run.py run --shop-id <SHOP_ID>        # kalan tüm ürünler
python run.py status                          # ilerleme
```

Çalışma yarıda kesilirse tekrar `run` dersen kaldığı yerden devam eder (SQLite `data/state.db`).

---

## Fiyatlandırma

Tedarikçi TL fiyatı → USD: `.env` içindeki `TRY_TO_USD` (güncel kuru gir) × `PRICE_MULTIPLIER` (marj+komisyon+kargo). Sonuç `.99`'a yuvarlanır. Yayınlamadan önce kontrol et.

## Ayarlar (config.yaml)

- `listing.state: draft` — **incelemeden canlıya alma.** Hazır olunca `active` yap.
- `listing.who_made / when_made / is_supply` — Etsy zorunlu alanları.
- `images.generate_lifestyle` + `lifestyle_count` — AI görsel üretimi (maliyetli; ürün başına çağrı). Önce küçük tut.
- `etsy.dry_run: true` — Etsy'ye hiçbir şey yazmadan tüm akışı dener.
- `etsy.requests_per_second` — Etsy limiti 10/sn, 10.000/gün. 1500 ürün × (~1 ilan + ~5 görsel) ≈ 9.000 istek; günlük limite yakın, gerekirse 2 güne yay.

## Önemli uyarılar

- **Etsy ürün politikası:** Etsy "handmade / vintage / craft supply" pazarıdır. Tedarikçi malını birebir reselling Etsy kurallarına aykırı olabilir ve mağaza kapatılabilir. Ürünleri uygun şekilde (kendi tasarımın / production partner tanımı) listelediğinden emin ol.
- **Telif:** Tedarikçi görselleri/metinleri için kullanım hakkına sahip olduğundan emin ol.
- **Listeleme ücreti:** Etsy ilan başına ~$0.20 alır. 1500 taslak yayınlanınca ≈ $300.
- İlk çalıştırmayı her zaman `--limit 1 --dry-run` ile yap.

## Dosya yapısı

```
run.py                  # CLI
config.yaml(.example)   # ayarlar
.env(.example)          # anahtarlar
etsy_lister/
  config.py             # ayar yükleme
  etsy_auth.py          # OAuth2 PKCE + token yenileme
  etsy_client.py        # API istemcisi (rate limit, retry)
  scraper.py            # Ticimax/bamyum veri çekme + kategori gezme
  optimizer.py          # GPT/Gemini İngilizce SEO içerik
  images.py             # tedarikçi görsel indirme + AI lifestyle üretme
  store.py              # SQLite durum (resume/idempotent)
  pipeline.py           # uçtan uca akış
data/                   # token, state.db, indirilen görseller (otomatik)
```
