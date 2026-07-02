#!/usr/bin/env python3
"""
create_listing.py — Tek görselden Etsy listing oluşturur.
Kullanım: python create_listing.py foto.jpg
"""
from __future__ import annotations
import base64, json, os, subprocess, sys, tempfile, time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env", override=True)

sys.path.insert(0, str(ROOT))
from etsy_lister.etsy_client import EtsyClient

GEMINI_KEY  = os.getenv("GEMINI_API_KEY", "")
FAL_KEY     = os.getenv("FAL_KEY", "")
ETSY_KEY    = os.getenv("ETSY_API_KEY", "")
ETSY_SECRET = os.getenv("ETSY_API_SECRET", "")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
FAL_BASE    = "https://fal.run"

SHOP_ID           = 66204430
RETURN_POLICY_ID  = 1489092630994
READINESS_STATE   = 1489523648274

SECTIONS = [
    ("Copper Sink",         58732502),
    ("Pendant Lamp",        58731760),
    ("Bird Baths",          58782483),
    ("Wall Decor",          58748469),
    ("Faucet",              58917530),
    ("Garden",              58771291),
    ("Bath and Shower Set", 58793765),
    ("Spa",                 58958340),
    ("Kitchen Sink",        59080269),
    ("Bathroom Sink",       59063930),
]

# shape → [(label, inch_val), ...]
SIZE_VARIATIONS: dict[str, list[tuple[str, int]]] = {
    "round": [
        ("14 inch / 36 cm", 14),
        ("16 inch / 41 cm", 16),
        ("18 inch / 46 cm", 18),
        ("20 inch / 51 cm", 20),
        ("22 inch / 56 cm", 22),
    ],
    "oval": [
        ("16x12 in / 41x30 cm", 16),
        ("18x13 in / 46x33 cm", 18),
        ("20x14 in / 51x36 cm", 20),
        ("22x15 in / 56x38 cm", 22),
        ("24x16 in / 61x41 cm", 24),
    ],
    "rectangular": [
        ("15x12 in / 38x30 cm", 15),
        ("18x13 in / 46x33 cm", 18),
        ("20x14 in / 51x36 cm", 20),
        ("22x15 in / 56x38 cm", 22),
        ("24x18 in / 61x46 cm", 24),
    ],
    "square": [
        ("12x12 in / 30x30 cm", 12),
        ("14x14 in / 36x36 cm", 14),
        ("16x16 in / 41x41 cm", 16),
        ("18x18 in / 46x46 cm", 18),
        ("20x20 in / 51x51 cm", 20),
    ],
}


# ── Gemini helpers ────────────────────────────────────────────────────────────

def _gemini(endpoint: str, payload: dict, retries: int = 5) -> dict:
    """Gemini çağrısı — geçici hatalarda (503/429/500/502/504) otomatik tekrar dener."""
    transient = {429, 500, 502, 503, 504}
    last_err = ""
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{GEMINI_BASE}/{endpoint}",
                headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
                json=payload,
                timeout=180,
            )
        except requests.RequestException as e:
            last_err = f"bağlantı hatası: {e}"
            time.sleep(min(2 ** attempt, 15))
            continue
        if r.ok:
            return r.json()
        last_err = f"Gemini {r.status_code}: {r.text[:200]}"
        # Geçici yoğunluk hataları → bekle ve tekrar dene
        if r.status_code in transient and attempt < retries - 1:
            wait = min(2 ** attempt, 15)  # 1, 2, 4, 8, 15... sn
            print(f"  Gemini {r.status_code} (geçici), {wait}sn sonra tekrar deneniyor "
                  f"({attempt + 1}/{retries})...", flush=True)
            time.sleep(wait)
            continue
        # Kalıcı hata (400/401/403 vb.) → hemen bildir
        raise RuntimeError(last_err)
    raise RuntimeError(f"Gemini {retries} denemede yanıt vermedi. Son hata: {last_err}")


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ── 1. Görsel analizi ─────────────────────────────────────────────────────────

def _to_jpeg_if_needed(image_path: str) -> tuple[str, bool]:
    """AVIF/WEBP/HEIC → JPEG. Pillow ile (macOS + Linux uyumlu)."""
    ext = image_path.lower().rsplit(".", 1)[-1]
    if ext in ("avif", "webp", "heic", "heif"):
        from PIL import Image
        try:
            import pillow_avif  # noqa: F401 — AVIF desteğini kaydeder
        except ImportError:
            pass
        try:
            import pillow_heif  # noqa: F401 — HEIC/HEIF desteğini kaydeder
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        with Image.open(image_path) as im:
            im.convert("RGB").save(tmp.name, "JPEG", quality=92)
        return tmp.name, True
    return image_path, False


def analyze_image(image_path: str) -> dict:
    print("Görsel analiz ediliyor...")
    work_path, converted = _to_jpeg_if_needed(image_path)
    ext  = work_path.lower().rsplit(".", 1)[-1]
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
    with open(work_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    if converted:
        Path(work_path).unlink(missing_ok=True)

    prompt = """Analyze this handmade product image and return ONLY a JSON object:
{
  "product_name": "concise English product name",
  "product_type": "type of product",
  "main_material": "primary material e.g. copper",
  "secondary_materials": ["list of other materials"],
  "color_finish": "color and surface finish",
  "style": "design style e.g. rustic, modern, artisan",
  "room_type": "where used e.g. bathroom, kitchen, garden",
  "key_features": ["feature1", "feature2", "feature3"],
  "shape": "round OR oval OR rectangular OR square — best guess from image"
}
Return only valid JSON, no markdown, no explanation."""

    resp = _gemini(
        "models/gemini-2.5-flash:generateContent",
        {
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": img_b64}},
            ]}],
            "generationConfig": {"temperature": 0.1},
        },
    )
    return _parse_json(resp["candidates"][0]["content"]["parts"][0]["text"])


# ── 2. Görsel üretimi ─────────────────────────────────────────────────────────

IMAGE_SCENES = [
    # Flux Kontext için doğru format:
    # — Düzenleme komutu değil, nihai fotoğrafın kısa tanımı
    # — Sahnenin ışığı ürünü nasıl vuruyor → model ürünü yeniden aydınlatır
    # — Ürünün ortamla fiziksel etkileşimi (gölge, yansıma) kısaca belirtilir
    # — Kısa (60-100 kelime) ve doğal dil

    ("lifestyle",
     "Interior design photograph of a handcrafted copper piece on a white Calacatta marble "
     "bathroom vanity. Warm recessed downlight from above illuminates the hammered copper surface, "
     "casting a soft shadow onto the marble below. Frosted window light adds cool fill from the "
     "left. The copper's warm patina reflects faint grey tones from the marble veining. "
     "Rolled linen towels and brushed chrome faucet softly blurred behind. "
     "35mm lens, f/2.8. Architectural Digest. Photorealistic."),

    ("lifestyle",
     "Interior photograph of a handcrafted copper piece in a farmhouse kitchen, "
     "photographed at golden hour. Warm amber window light from the right rakes across "
     "the hammered copper surface, matching the warm tone of the reclaimed oak butcher-block "
     "beneath it. The copper reflects warm wood tones from the island below. "
     "Cast-iron pan and ceramic crocks softly blurred on open shelves behind. "
     "35mm, f/2.0, Kodak Portra. Photorealistic."),

    ("lifestyle",
     "Restaurant interior photograph. A handcrafted copper piece above a white-linen dinner "
     "table set for two. Candlelight from the table below bounces warm amber light upward, "
     "catching the copper's hammered texture and patina. The piece casts a warm glow "
     "downward onto the crystal glasses and silverware. Exposed brick wall in soft focus behind. "
     "50mm, f/1.8, ISO 800, available light. Fine dining ambiance. Photorealistic."),

    ("lifestyle",
     "Kinfolk magazine interior photograph. A handcrafted copper piece in a bright "
     "Scandinavian living room. Flat overcast Nordic daylight from a large window fills the "
     "room evenly, wrapping cleanly around the copper surface. "
     "The copper's warm patina is the only warm element against pale oak floors, "
     "white plaster walls, and a linen sofa. Soft shadow beneath the piece, no harsh edges. "
     "24mm, f/5.6, full depth of field. Airy and minimal. Photorealistic."),

    ("lifestyle",
     "Architectural photograph of a handcrafted copper piece as the centrepiece of a boutique "
     "hotel lobby. Overhead track spotlights from the concrete ceiling illuminate the copper "
     "from above, creating strong top-lit highlights on the hammered surface. "
     "The copper casts a warm amber reflection onto the geometric terrazzo floor below. "
     "Moss wall panel behind, marble reception desk blurred in foreground. "
     "17mm, f/8, long exposure. Photorealistic."),

    ("lifestyle",
     "Lifestyle photograph of a handcrafted copper piece in a bohemian bedroom at golden hour. "
     "Warm side light from a window on the left wraps around the copper surface, "
     "its hammered patina glowing in the amber light. The copper's warm tones blend naturally "
     "with the terracotta plaster wall and rattan headboard behind. "
     "Soft shadow on the nearby surface grounds the piece in the space. "
     "85mm, f/1.8, creamy bokeh. Kinfolk quality. Photorealistic."),

    ("closeup",
     "Macro studio photograph. The copper product's hammered surface fills the entire frame. "
     "Single softbox at 45° upper-left creates directional light — specular highlights on the "
     "raised hammer peaks, deep micro-shadows in each indentation. "
     "Patina shifts from warm terracotta orange at peaks to deep verdigris green in recesses. "
     "Thin rim-light from the right separates the copper from a clean white background. "
     "Macro lens, f/8, ISO 100. Ultra-sharp, commercial print quality."),
]

_GEMINI_IMAGE_MODELS = [
    "models/gemini-2.5-flash-preview-image-generation",
    "models/gemini-2.0-flash-preview-image-generation",
]


def _fal_flux_kontext(prompt: str, ref_b64: str, ref_mime: str) -> bytes | None:
    """fal.ai Flux Kontext — referans ürünü aynen koruyarak sahneyi düzenler."""
    if not FAL_KEY:
        return None
    data_uri = f"data:{ref_mime};base64,{ref_b64}"
    for model in ["fal-ai/flux-pro/kontext/max", "fal-ai/flux-pro/kontext"]:
        for attempt in range(2):
            try:
                r = requests.post(
                    f"{FAL_BASE}/{model}",
                    headers={
                        "Authorization": f"Key {FAL_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "prompt": prompt,
                        "image_url": data_uri,
                        "num_images": 1,
                        "image_size": "square_hd",
                    },
                    timeout=180,
                )
                if r.ok:
                    images = r.json().get("images", [])
                    if images:
                        img_url = images[0].get("url", "")
                        if img_url:
                            dl = requests.get(img_url, timeout=60)
                            if dl.ok:
                                return dl.content
                elif r.status_code in (402, 404):
                    # 402 = kredi bitti, 404 = model yanlış — sonraki modeli dene
                    if r.status_code == 402:
                        print(f"\n  fal.ai kredi bitti ({model})")
                        return None
                    break  # 404: bu model yok, sonrakini dene
                elif r.status_code == 422:
                    print(f"\n  fal.ai parametre hatası: {r.text[:200]}")
                    break
                else:
                    if attempt == 1:
                        print(f"\n  fal.ai {r.status_code}: {r.text[:200]}")
                        break
                    time.sleep(3)
            except Exception as e:
                if attempt == 1:
                    print(f"\n  fal.ai bağlantı hatası: {e}")
                time.sleep(2)
    return None


def _gemini_image(prompt: str, ref_b64: str, ref_mime: str) -> bytes | None:
    """Gemini image modelleri — yedek."""
    for model in _GEMINI_IMAGE_MODELS:
        for attempt in range(2):
            try:
                resp = _gemini(
                    f"{model}:generateContent",
                    {
                        "contents": [{"parts": [
                            {"inline_data": {"mime_type": ref_mime, "data": ref_b64}},
                            {"text": prompt},
                        ]}],
                        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
                    },
                )
                for part in resp.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                    if "inlineData" in part:
                        return base64.b64decode(part["inlineData"]["data"])
            except Exception:
                if attempt == 1:
                    break
                time.sleep(1)
    return None


def _generate_one(prompt: str, ref_b64: str, ref_mime: str) -> bytes | None:
    """fal.ai Flux Kontext (birincil) → Gemini (yedek)."""
    result = _fal_flux_kontext(prompt, ref_b64, ref_mime)
    if result:
        return result
    print(" [Gemini'ye geçiliyor...]", end="", flush=True)
    return _gemini_image(prompt, ref_b64, ref_mime)


def generate_images(info: dict, image_path: str, out_dir: Path,
                     on_image=None) -> list[Path]:
    total = len(IMAGE_SCENES)
    engine = "fal.ai Flux Kontext" if FAL_KEY else "Gemini"
    print(f"{total} görsel üretiliyor [{engine}] (2-4 dakika sürebilir)...")
    out_dir.mkdir(parents=True, exist_ok=True)

    work_path, converted = _to_jpeg_if_needed(image_path)
    ref_mime = "image/jpeg"
    with open(work_path, "rb") as f:
        ref_b64 = base64.b64encode(f.read()).decode()
    if converted:
        Path(work_path).unlink(missing_ok=True)

    saved: list[Path] = []
    for i, (scene_type, scene_prompt) in enumerate(IMAGE_SCENES, 1):
        print(f"  {i}/{total} üretiliyor ({scene_type})...", end="", flush=True)
        data = _generate_one(scene_prompt, ref_b64, ref_mime)
        if data:
            p = out_dir / f"image_{i:02d}.jpg"
            p.write_bytes(data)
            saved.append(p)
            if on_image:
                on_image(i, total, scene_type, str(p))
            print(" ✓")
        else:
            print(" ✗ atlandı")
        time.sleep(1)

    print(f"{len(saved)}/{total} görsel kaydedildi → {out_dir}")
    return saved


# ── 3. İçerik üretimi ────────────────────────────────────────────────────────

def generate_content(info: dict, sizes: list[tuple[str, int]]) -> dict:
    print("Başlık, açıklama ve taglar yazılıyor...")
    size_list = "\n".join(f"- {s[0]}" for s in sizes)
    prompt = f"""You are an expert Etsy SEO copywriter for a handmade copper goods shop.

Product:
- Name: {info['product_name']}
- Material: Copper
- Style: {info['style']}
- Finish: {info['color_finish']}
- Features: {', '.join(info.get('key_features', []))}
- Room: {info['room_type']}
- Available sizes: {size_list}

Return ONLY this JSON:
{{
  "title": "SEO Etsy title max 130 chars, front-load: material + product + style keywords, no ALL CAPS",
  "description": "Write a 200-250 word SEO-focused listing description. Structure:\n1. First line: '✦ FREE SHIPPING to United States ✦'\n2. One hook sentence about the product.\n3. Key features as bullet points (5-6 bullets with •).\n4. Available sizes section listing all sizes.\n5. Brief care note (1-2 sentences).\n6. Made-to-order note.\nKeep it concise and keyword-rich.",
  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10","tag11","tag12","tag13"]
}}

Tag rules: exactly 13 tags, each max 20 chars. Mix: 4-5 short broad tags (e.g. "copper sink") + 7-8 long-tail 2-3 word phrases (e.g. "handmade copper sink", "rustic vessel sink", "farmhouse bathroom decor"). No repeats.
Return only valid JSON, no markdown."""

    resp = _gemini(
        "models/gemini-2.5-flash:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7},
        },
    )
    return _parse_json(resp["candidates"][0]["content"]["parts"][0]["text"])


# ── 4. Ücretsiz kargo profili ────────────────────────────────────────────────

def get_free_shipping_profile(client: EtsyClient) -> int | None:
    profiles = client.get_shipping_profiles(SHOP_ID)
    for p in profiles.get("results", []):
        title = p.get("title", "").lower()
        if "free" in title or "0" in title:
            return p["shipping_profile_id"]
        for dest in p.get("shipping_profile_destinations", []):
            if dest.get("primary_cost", {}).get("amount", -1) == 0:
                return p["shipping_profile_id"]
    return None


def ensure_free_shipping_profile(client: EtsyClient) -> int:
    existing = get_free_shipping_profile(client)
    if existing:
        return existing

    # Get origin country from first existing profile
    profiles = client.get_shipping_profiles(SHOP_ID)
    origin = "TR"
    results = profiles.get("results", [])
    if results:
        origin = results[0].get("origin_country_iso", "TR")

    print("Ücretsiz kargo profili oluşturuluyor...")
    new_profile = client._request(
        "POST", f"/shops/{SHOP_ID}/shipping-profiles",
        json={
            "title": "Free Shipping",
            "origin_country_iso": origin,
            "primary_cost": 0.00,
            "secondary_cost": 0.00,
            "destination_country_iso": "US",
            "min_processing_days": 4,
            "max_processing_days": 10,
        },
    )
    pid = new_profile["shipping_profile_id"]
    print(f"  Ücretsiz kargo profili oluşturuldu: {pid}")
    return pid


# ── 5. Varyasyon (ölçü) ───────────────────────────────────────────────────────

def add_size_variations(client: EtsyClient, listing_id: int, sizes: list[tuple[str, int]], price: float):
    print("Ölçü varyasyonları ekleniyor...")
    products = [
        {
            "sku": f"SIZE-{inch}",
            "property_values": [{
                "property_id": 513,
                "property_name": "Size",
                "scale_id": None,
                "value_ids": [],
                "values": [label],
            }],
            "offerings": [{
                "price": price,
                "quantity": 10,
                "is_enabled": True,
            }],
        }
        for label, inch in sizes
    ]
    try:
        client._request(
            "PUT", f"/shops/{SHOP_ID}/listings/{listing_id}/inventory",
            json={
                "products": products,
                "price_on_property": [],
                "quantity_on_property": [],
                "sku_on_property": [],
            },
        )
        print(f"  {len(sizes)} ölçü eklendi.")
    except Exception as e:
        print(f"  Varyasyon eklenemedi (Etsy API): {e}")
        print("  → Etsy panelinden manuel ekleyebilirsin.")


# ── UI helper ─────────────────────────────────────────────────────────────────

def pick(items: list, label: str):
    print(f"\n{label}:")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item[0]}")
    while True:
        try:
            idx = int(input("Numara seç: ")) - 1
            if 0 <= idx < len(items):
                return items[idx]
        except (ValueError, KeyboardInterrupt):
            pass
        print("Geçersiz, tekrar dene.")


# ── Ana akış ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        sys.exit("Kullanım: python create_listing.py foto.jpg")

    image_path = sys.argv[1]
    if not Path(image_path).exists():
        sys.exit(f"Dosya bulunamadı: {image_path}")
    if not FAL_KEY and not GEMINI_KEY:
        sys.exit("FAL_KEY veya GEMINI_API_KEY .env dosyasında eksik.")
    if not ETSY_KEY:
        sys.exit("ETSY_API_KEY .env dosyasında eksik.")

    # 1. Görsel analizi
    info = analyze_image(image_path)
    shape = info.get("shape", "round").lower()
    if shape not in SIZE_VARIATIONS:
        shape = "round"
    sizes = SIZE_VARIATIONS[shape]
    print(f"  → {info['product_name']} | {info['main_material']} | {info['style']} | {shape}")

    # 2. Fiyat
    while True:
        try:
            price = float(input("\nFiyat (USD): $"))
            if price > 0:
                break
        except ValueError:
            pass
        print("Geçerli fiyat gir.")

    # 3. Mağaza bölümü
    section_name, section_id = pick(SECTIONS, "Mağaza bölümü")

    # 4. Görseller
    out_dir = Path(image_path).parent / f"listing_{int(time.time())}"
    images = generate_images(info, image_path, out_dir)
    if not images:
        sys.exit("Hiç görsel üretilemedi, çıkılıyor.")

    # 5. İçerik
    content = generate_content(info, sizes)
    print(f"\nBaşlık: {content['title']}")
    print(f"Taglar: {', '.join(content['tags'][:5])}...")

    # 6. Ücretsiz kargo profili
    client = EtsyClient(ETSY_KEY, ETSY_SECRET)
    shipping_id = ensure_free_shipping_profile(client)

    # 7. Etsy draft listing
    print("\nEtsy listing oluşturuluyor...")
    fields = {
        "title":               content["title"][:140],
        "description":         content["description"],
        "price":               price,
        "quantity":            10,
        "who_made":            "i_did",
        "when_made":           "made_to_order",
        "is_supply":           False,
        "taxonomy_id":         11353,
        "shipping_profile_id": shipping_id,
        "return_policy_id":    RETURN_POLICY_ID,
        "shop_section_id":     section_id,
        "tags":                [t[:20] for t in content["tags"][:13]],
        "materials":           ["Copper"],
        "state":               "draft",
        "readiness_state_id":  READINESS_STATE,
        "should_auto_renew":   True,
        "is_customizable":     True,
    }

    listing = client.create_draft_listing(SHOP_ID, fields)
    lid = listing["listing_id"]
    print(f"Draft oluşturuldu: listing_id={lid}")

    # 8. Görselleri yükle
    print("Görseller yükleniyor...")
    for rank, img_path in enumerate(images, 1):
        print(f"  {rank}/{len(images)} yükleniyor...", end="\r", flush=True)
        client.upload_listing_image(SHOP_ID, lid, str(img_path), rank=rank)
        time.sleep(0.4)
    print(f"\n{len(images)} görsel yüklendi.")

    # 9. Ölçü varyasyonları
    add_size_variations(client, lid, sizes, price)

    # 10. TASLAK olarak bırakılır — publish EDİLMEZ.
    print(f"\nTaslak oluşturuldu! Etsy'de inceleyip kendin yayınlayabilirsin.")
    print(f"https://www.etsy.com/your/shops/me/tools/listings/{lid}")


if __name__ == "__main__":
    main()
