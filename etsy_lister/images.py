"""Image handling: download supplier photos and (optionally) generate lifestyle shots.

All images are saved locally and returned as file paths, ready for uploadListingImage.
Etsy needs >=2000px on the shortest side ideally; supplier "buyuk" images are used as-is.
"""
from __future__ import annotations
import base64
import io
from pathlib import Path

import requests
from PIL import Image

from .config import DATA_DIR

IMG_DIR = DATA_DIR / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0"}

LIFESTYLE_SCENES = [
    "hanging above a wooden dining table in a cozy modern restaurant, warm evening ambiance, softly blurred diners",
    "in a bright Scandinavian living room above a light-oak coffee table, soft daylight, plants",
    "over a kitchen island in a warm modern farmhouse kitchen, morning light",
    "in a trendy cafe above a marble counter, warm pendant lighting, brick wall",
    "in an elegant boutique hotel lobby, sophisticated warm lighting",
]


def _slug(s: str, n: int = 40) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower())[:n].strip("-")


def download_supplier_images(product: dict, max_count: int) -> list[str]:
    paths = []
    folder = IMG_DIR / _slug(product.get("sku") or product.get("title", "item"))
    folder.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(product.get("image_urls", [])[:max_count]):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            out = folder / f"supplier_{i+1}.jpg"
            img.save(out, "JPEG", quality=92)
            paths.append(str(out))
        except Exception as e:  # noqa: BLE001
            print(f"  ! image download failed {url}: {e}")
    return paths


def generate_lifestyle_images(product: dict, reference_path: str | None,
                              count: int, openai_key: str,
                              model: str = "gpt-image-1") -> list[str]:
    """Generate `count` lifestyle images. Uses the supplier photo as a reference
    (image edit) when available, else text-to-image."""
    from openai import OpenAI
    client = OpenAI(api_key=openai_key)
    folder = IMG_DIR / _slug(product.get("sku") or product.get("title", "item"))
    folder.mkdir(parents=True, exist_ok=True)
    title = product.get("title", "product")
    base_desc = (f"Keep this exact product unchanged (same shape, material, color, finish). "
                 f"Photorealistic lifestyle interior photography of: {title}. Place it ")
    paths = []
    for i in range(count):
        scene = LIFESTYLE_SCENES[i % len(LIFESTYLE_SCENES)]
        prompt = base_desc + scene + ". Magazine-quality, natural light, shallow depth of field."
        try:
            if reference_path:
                with open(reference_path, "rb") as fh:
                    resp = client.images.edit(model=model, image=fh, prompt=prompt,
                                              size="1024x1024")
            else:
                resp = client.images.generate(model=model, prompt=prompt, size="1024x1024")
            b64 = resp.data[0].b64_json
            out = folder / f"lifestyle_{i+1}.jpg"
            Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB").save(
                out, "JPEG", quality=92)
            paths.append(str(out))
        except Exception as e:  # noqa: BLE001
            print(f"  ! lifestyle generation failed: {e}")
            break
    return paths
