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

# OpenAI gpt-image-1 — placeholder ise yok say
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if OPENAI_KEY.startswith("BURAYA") or OPENAI_KEY.lower().startswith("your"):
    OPENAI_KEY = ""
# Görsel kalitesi: "low" | "medium" | "high" (env ile değiştirilebilir)
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium").strip().lower()
# DENEME MODU: kaç görsel üretilsin (token tasarrufu). Şu an 1; normalde 10.
# Render'da MAX_IMAGES=10 env'i ile ya da bu satırı 10 yaparak geri açılır.
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "1"))

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
FAL_BASE    = "https://fal.run"
OPENAI_BASE = "https://api.openai.com/v1"

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


def _num(x) -> str:
    """8.0 -> '8', 10.67 -> '10.7' (gereksiz ondalık sıfırı at)."""
    r = round(float(x), 1)
    return str(int(r)) if r == int(r) else str(r)


def suggest_sizes(info: dict, image_path: str | None = None) -> list[dict]:
    """Görseli analiz edip ürünün EN/BOY oranını + ABD'de popüler 'en' ölçülerini alır,
    her ölçünün BOYUNU orana göre HESAPLAR (Gemini'nin matematiğine güvenmeden).
    Döner: [{"inch": "16 x 12 in", "cm": "41 x 30 cm", "label": "16 x 12 in / 41 x 30 cm"}, ...]"""
    ptype = info.get("product_type") or info.get("product_name") or "product"

    parts: list[dict] = []
    if image_path:  # görseli ekle — Gemini ürünün gerçek oranını görsün
        try:
            work, conv = _to_jpeg_if_needed(image_path)
            with open(work, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            if conv:
                Path(work).unlink(missing_ok=True)
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
        except Exception:
            pass

    prompt = f"""Look at this product image of a {ptype} (material: {info.get('main_material','copper')}).
Do TWO things:
1. Determine the product's real WIDTH-to-HEIGHT ratio (width divided by height) from its ACTUAL shape in the image, ignoring the background. Examples: a tall pendant lamp ~0.7, a wide shallow vessel sink ~2.5, a square item ~1.0, a bird bath ~1.6.
2. List the 5 MOST POPULAR United States market WIDTH sizes (in whole inches) for this exact product type, smallest to largest.

Return ONLY this JSON:
{{
  "ratio_w_to_h": 0.75,
  "widths_inch": [8, 12, 16, 20, 24]
}}
Return only valid JSON, no markdown."""
    parts.append({"text": prompt})

    try:
        resp = _gemini(
            "models/gemini-2.5-flash:generateContent",
            {"contents": [{"parts": parts}],
             "generationConfig": {"temperature": 0.2}},
        )
        data = _parse_json(resp["candidates"][0]["content"]["parts"][0]["text"])
        ratio = float(data.get("ratio_w_to_h", 0) or 0)
        widths = data.get("widths_inch", []) or []
        if ratio > 0 and widths:
            sizes = []
            for w in widths[:5]:
                w = float(w)
                h = w / ratio                        # boy = en ÷ (en/boy oranı)
                wc, hc = round(w * 2.54), round(h * 2.54)
                inch = f"{_num(w)} x {_num(h)} in"
                cm = f"{wc} x {hc} cm"
                sizes.append({"inch": inch, "cm": cm, "label": f"{inch} / {cm}"[:45]})
            if sizes:
                return sizes
    except Exception as e:
        print(f"  suggest_sizes başarısız ({e}), sabit ölçülere düşülüyor.")
    # Fallback: shape'e göre sabit tablo
    shape = (info.get("shape") or "round").lower()
    if shape not in SIZE_VARIATIONS:
        shape = "round"
    return [{"inch": "", "cm": "", "label": lbl} for lbl, _ in SIZE_VARIATIONS[shape]]


# ── 2. Görsel üretimi ─────────────────────────────────────────────────────────

# Her prompt'un başına eklenen ürün koruma kilidi — ürün birebir korunur.
PRODUCT_LOCK = (
    "Recreate the EXACT product shown in the reference image as a high-quality, professional "
    "1:1 square Etsy product photograph. Preserve its design, form, proportions, color, "
    "material, texture and every distinctive detail completely faithful to the reference — "
    "do not redesign, restyle, or alter the product itself in any way. Keep the product as the "
    "main focus. Only build a new scene and background around this same, unchanged product. "
    "Place the product in the scene exactly the way this type of product is used in real life: "
    "a hanging lamp, pendant or chandelier hangs from the ceiling and is lit; a sink or basin is "
    "installed into a countertop or vanity; a cup, bowl, vase or planter sits on a surface; wall "
    "decor is mounted on a wall. Never position the product in a physically impossible or wrong way. "
)

# Lifestyle görselleri için ortak kurallar
_LIFESTYLE_RULES = (
    " Lighting must be natural and realistic — never artificial, never overexposed, never "
    "fake-looking. The product stays the clear focal point; the scene must not distract from it. "
    "Absolutely NO text, NO logos, NO watermarks, and NO people anywhere. "
    "Clean, premium, attention-grabbing, Etsy-listing quality. Strict 1:1 square composition."
)

# İnfografik görselleri için ortak kurallar
_INFOGRAPHIC_RULES = (
    " Every piece of text MUST be written in clearly legible, professionally typeset ENGLISH. "
    "Spell every word correctly and EXACTLY as given — no typos, no missing or invented letters. "
    "Keep the layout clean, premium and uncluttered; the product remains the focal point. "
    "Strict 1:1 square composition suitable for an Etsy listing."
)

# 8 lifestyle/ürün sahnesi (çeşitli çekim açıları) + 2 İngilizce infografik = 10 görsel
IMAGE_SCENES = [
    # 1 — Uzak / geniş çekim, lüks iç mekan
    ("lifestyle",
     "Wide establishing shot set in an elegant luxury marble bathroom interior with tasteful "
     "minimal decor. Soft natural window light with realistic shadows. The full product is "
     "clearly visible while the surroundings stay softly out of focus."),

    # 2 — Açısal (45°) çekim, sıcak iç mekan
    ("lifestyle",
     "Three-quarter 45-degree angle shot set in a warm modern farmhouse interior. Soft morning "
     "daylight rakes gently across the product to reveal its form and material; cozy details sit "
     "blurred behind with shallow depth of field. Product sharp and dominant."),

    # 3 — Yakın çekim, ürün odaklı
    ("lifestyle",
     "Tight close-up shot with a warm neutral interior gently blurred behind. Soft directional "
     "daylight reveals the product's true color and surface texture. The product is the "
     "unmistakable hero of the frame."),

    # 4 — Detay / makro çekim
    ("lifestyle",
     "Extreme macro detail shot focusing on the product's surface texture, material and finish. "
     "Soft natural raking light emphasizes the craftsmanship and fine details with a shallow "
     "focus falloff. Rich, tactile and realistic — no artificial gloss."),

    # 5 — Perspektif / alçak açı, premium sunum
    ("lifestyle",
     "Low-angle perspective shot in a clean, minimal gallery-like space with a soft neutral "
     "background. Gentle natural light and a grounded, premium presentation with the product "
     "commanding the frame."),

    # 6 — Lifestyle, aydınlık modern yaşam alanı
    ("lifestyle",
     "Set in a bright, airy modern living space with abundant soft natural daylight and a few "
     "minimal, tasteful decor elements. Realistic, lived-in yet premium mood, with the product "
     "clearly dominant in the composition."),

    # 7 — Lifestyle, doğal / sıcak mekan
    ("lifestyle",
     "Set in a serene natural setting with soft late-afternoon sunlight and out-of-focus "
     "greenery. Organic, calm, natural atmosphere with realistic light and gentle shadows. "
     "Product sharp and central."),

    # 8 — Premium stüdyo / katalog çekimi
    ("lifestyle",
     "Clean premium studio catalog shot with a smooth neutral gradient background. Professional "
     "daylight-balanced studio lighting with a natural soft shadow. Crisp, high-end e-commerce "
     "look, product perfectly presented."),

    # 9 — İnfografik: ürün özellikleri (İngilizce)
    ("infographic",
     "Clean premium product infographic on a soft neutral background. The product is centered "
     "with 3 to 4 minimal thin callout lines pointing to key selling points. Short ENGLISH "
     "labels only: 'Handmade', 'Premium Material', 'Durable Finish', 'Custom Sizes'. Modern, "
     "elegant English sans-serif typography with generous white space, professional e-commerce style."),

    # 10 — İnfografik: kalite ve kullanım (İngilizce)
    ("infographic",
     "Clean product infographic highlighting quality and use, on a soft neutral background. "
     "The product is shown prominently with 2 to 3 short ENGLISH feature captions such as "
     "'Handcrafted Quality', 'Built to Last', and 'Perfect for Home & Gifting'. Minimal modern "
     "English typography in a premium, uncluttered layout."),
]


def _compose_prompt(scene_type: str, scene_desc: str) -> str:
    """Ürün kilidi + sahne + tür kurallarını birleştirir."""
    rules = _INFOGRAPHIC_RULES if scene_type == "infographic" else _LIFESTYLE_RULES
    return PRODUCT_LOCK + scene_desc + rules

_GEMINI_IMAGE_MODELS = [
    "models/gemini-2.5-flash-preview-image-generation",
    "models/gemini-2.0-flash-preview-image-generation",
]


def _openai_image(prompt: str, ref_b64: str, ref_mime: str) -> bytes | None:
    """OpenAI gpt-image-1 — referans ürünü koruyarak yeni sahne üretir (/images/edits)."""
    if not OPENAI_KEY:
        return None
    ref_bytes = base64.b64decode(ref_b64)
    ext = "png" if "png" in ref_mime else "jpg"
    for attempt in range(3):
        try:
            r = requests.post(
                f"{OPENAI_BASE}/images/edits",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                files={"image": (f"ref.{ext}", ref_bytes, ref_mime)},
                data={
                    "model": "gpt-image-1",
                    "prompt": prompt[:30000],
                    "size": "1024x1024",
                    "quality": OPENAI_IMAGE_QUALITY,
                    "n": "1",
                },
                timeout=300,
            )
            if r.ok:
                data = r.json().get("data", [])
                if data and data[0].get("b64_json"):
                    return base64.b64decode(data[0]["b64_json"])
                return None
            body = r.text[:250]
            if r.status_code in (401, 403):
                # Geçersiz anahtar VEYA organizasyon doğrulaması gerekli
                print(f"\n  OpenAI {r.status_code}: {body}", flush=True)
                return None
            if r.status_code == 400:
                print(f"\n  OpenAI 400 (istek/içerik reddi): {body}", flush=True)
                return None
            if r.status_code == 429:  # yoğunluk / kota
                wait = 6 * (attempt + 1)
                print(f"\n  OpenAI 429 (yoğun), {wait}sn sonra tekrar...", flush=True)
                time.sleep(wait)
                continue
            if attempt < 2:  # 5xx → tekrar dene
                time.sleep(3 * (attempt + 1))
                continue
            print(f"\n  OpenAI {r.status_code}: {body}", flush=True)
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"\n  OpenAI bağlantı hatası: {e}", flush=True)
    return None


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
    """OpenAI gpt-image-1 (birincil) → fal.ai Flux Kontext → Gemini (yedek)."""
    if OPENAI_KEY:
        result = _openai_image(prompt, ref_b64, ref_mime)
        if result:
            return result
        print(" [fal.ai'ye geçiliyor...]", end="", flush=True)
    result = _fal_flux_kontext(prompt, ref_b64, ref_mime)
    if result:
        return result
    print(" [Gemini'ye geçiliyor...]", end="", flush=True)
    return _gemini_image(prompt, ref_b64, ref_mime)


def generate_images(info: dict, image_path: str, out_dir: Path,
                     on_image=None) -> list[Path]:
    scenes = IMAGE_SCENES[:max(1, MAX_IMAGES)]
    total = len(scenes)
    if OPENAI_KEY:
        engine = f"OpenAI gpt-image-1 ({OPENAI_IMAGE_QUALITY})"
    elif FAL_KEY:
        engine = "fal.ai Flux Kontext"
    else:
        engine = "Gemini"
    print(f"{total} görsel üretiliyor [{engine}] (2-4 dakika sürebilir)...")
    out_dir.mkdir(parents=True, exist_ok=True)

    work_path, converted = _to_jpeg_if_needed(image_path)
    ref_mime = "image/jpeg"
    with open(work_path, "rb") as f:
        ref_b64 = base64.b64encode(f.read()).decode()
    if converted:
        Path(work_path).unlink(missing_ok=True)

    saved: list[Path] = []
    for i, (scene_type, scene_desc) in enumerate(scenes, 1):
        print(f"  {i}/{total} üretiliyor ({scene_type})...", end="", flush=True)
        prompt = _compose_prompt(scene_type, scene_desc)
        data = _generate_one(prompt, ref_b64, ref_mime)
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

def generate_content(info: dict, size_labels: list[str]) -> dict:
    print("Başlık, açıklama ve taglar yazılıyor...")
    size_list = "\n".join(f"- {lbl}" for lbl in size_labels)
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
  "description": "Write a 200-250 word SEO-focused listing description. Structure:\n1. First line: '✦ FREE SHIPPING to United States ✦'\n2. One hook sentence about the product.\n3. Key features as bullet points (5-6 bullets with •).\n4. Brief care note (1-2 sentences).\n5. Made-to-order note.\nKeep it concise and keyword-rich. Do NOT list any sizes, dimensions, or measurements anywhere in the description — the exact available sizes are appended automatically afterwards.",
  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10","tag11","tag12","tag13"]
}}

Tag rules — output EXACTLY 13 tags, and EVERY tag MUST be 20 characters or fewer (Etsy hard limit — count the characters of each tag before writing it). Weight tags by BUYER PURCHASE INTENT, not broad decor categories. Use this exact structure:
- 2 broad but relevant tags (e.g. "copper sink", "vessel sink")
- 7 high-intent long-tail tags combining product + material/finish + type/shape (e.g. "copper vessel sink", "hammered basin", "vanity sink", "round copper sink", "bathroom basin", "farmhouse sink", "custom size sink")
- 4 use-case / room / purchase-intent tags (e.g. "bathroom remodel", "powder room sink", "vanity basin", "rustic bathroom")
Prioritize product+material, product+mounting/use type, and product+room/use area — buyers search for exactly what they want to buy.
AVOID broad decor tags like "copper home decor", "rustic decor", "farmhouse decor", "handmade copper", "copper gift", "vintage decor". Use AT MOST 1-2 of them, and only inside the final 4 use/intent tags.
All tags lowercase, no duplicates, tailored to THIS specific product ({info.get('product_type', 'product')}). Never exceed 20 characters on any tag.
Return only valid JSON, no markdown."""

    resp = _gemini(
        "models/gemini-2.5-flash:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7},
        },
    )
    result = _parse_json(resp["candidates"][0]["content"]["parts"][0]["text"])
    # Ölçüleri Gemini'ye bırakma — seçilen ölçüleri DETERMINISTIK ekle (varyasyonlarla birebir aynı)
    if size_labels:
        block = "\n\n✦ AVAILABLE SIZES (Width x Height) ✦\n" + "\n".join(f"• {lbl}" for lbl in size_labels)
        result["description"] = result.get("description", "").rstrip() + block
    return result


def clean_tags(tags: list[str], limit: int = 13) -> list[str]:
    """Etsy tag kurallarını uygular: ≤20 karakter, küçük harf, tekrarsız, en fazla 13.
    20 karakteri aşan tag'i önce kelime sınırında kısaltmayı dener; olmazsa atlar."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        t = " ".join(str(raw).strip().lower().split())  # boşlukları normalize et
        if not t:
            continue
        if len(t) > 20:                                  # kelime atarak kısalt
            words = t.split()
            while words and len(" ".join(words)) > 20:
                words.pop()
            t = " ".join(words)
            if not t or len(t) > 20:                     # hâlâ uzunsa vazgeç
                continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


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

def add_size_variations(client: EtsyClient, listing_id: int, priced_sizes: list[dict]):
    """priced_sizes: [{"label": str, "price": float}, ...] — HER ölçü kendi fiyatıyla.
    Etsy'de fiyat 'Size' property'sine bağlanır (price_on_property=[513])."""
    print("Ölçü/fiyat varyasyonları ekleniyor...")
    products = []
    for i, ps in enumerate(priced_sizes, 1):
        products.append({
            "sku": f"SIZE-{i}",
            "property_values": [{
                "property_id": 513,
                "property_name": "Size",
                "scale_id": None,
                "value_ids": [],
                "values": [ps["label"]],
            }],
            "offerings": [{
                "price": round(float(ps["price"]), 2),
                "quantity": 10,
                "is_enabled": True,
                "readiness_state_id": READINESS_STATE,
            }],
        })
    try:
        client._request(
            "PUT", f"/listings/{listing_id}/inventory",
            json={
                "products": products,
                "price_on_property": [513],     # fiyat ölçüye göre DEĞİŞİR
                "quantity_on_property": [],
                "sku_on_property": [513],
            },
        )
        print(f"  {len(priced_sizes)} ölçü/fiyat eklendi.")
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
    if not OPENAI_KEY and not FAL_KEY and not GEMINI_KEY:
        sys.exit("OPENAI_API_KEY, FAL_KEY veya GEMINI_API_KEY .env dosyasında eksik.")
    if not ETSY_KEY:
        sys.exit("ETSY_API_KEY .env dosyasında eksik.")

    # 1. Görsel analizi
    info = analyze_image(image_path)
    print(f"  → {info['product_name']} | {info['main_material']} | {info['style']} | {info.get('shape','')}")

    # 2. Ölçüler (görselden oran + ABD popüler ölçüleri) + her ölçü için fiyat
    sizes = suggest_sizes(info, image_path)
    print("\nBu ürün için ABD'de popüler ölçüler — her biri için fiyat gir (boş = atla):")
    priced_sizes = []
    for s in sizes:
        while True:
            raw = input(f"  {s['label']} → $").strip()
            if not raw:
                break
            try:
                p = float(raw)
                if p > 0:
                    priced_sizes.append({"label": s["label"], "price": p})
                    break
            except ValueError:
                pass
            print("    Geçerli bir fiyat gir ya da boş bırak.")
    if not priced_sizes:
        sys.exit("En az bir ölçü için fiyat girmelisin.")
    base_price = min(ps["price"] for ps in priced_sizes)

    # 3. Mağaza bölümü
    section_name, section_id = pick(SECTIONS, "Mağaza bölümü")

    # 4. Görseller
    out_dir = Path(image_path).parent / f"listing_{int(time.time())}"
    images = generate_images(info, image_path, out_dir)
    if not images:
        sys.exit("Hiç görsel üretilemedi, çıkılıyor.")

    # 5. İçerik
    content = generate_content(info, [ps["label"] for ps in priced_sizes])
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
        "price":               base_price,
        "quantity":            10,
        "who_made":            "i_did",
        "when_made":           "made_to_order",
        "is_supply":           False,
        "taxonomy_id":         11353,
        "shipping_profile_id": shipping_id,
        "return_policy_id":    RETURN_POLICY_ID,
        "shop_section_id":     section_id,
        "tags":                clean_tags(content["tags"]),
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

    # 9. Ölçü/fiyat varyasyonları
    add_size_variations(client, lid, priced_sizes)

    # 10. TASLAK olarak bırakılır — publish EDİLMEZ.
    print(f"\nTaslak oluşturuldu! Etsy'de inceleyip kendin yayınlayabilirsin.")
    print(f"https://www.etsy.com/your/shops/me/tools/listings/{lid}")


if __name__ == "__main__":
    main()
