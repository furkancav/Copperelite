"""End-to-end orchestration for one product and for a full batch run."""
from __future__ import annotations
import math
import traceback

from . import store
from .config import Config
from .etsy_client import EtsyClient
from .scraper import scrape_product
from .optimizer import optimize
from . import images as imgmod


def compute_price_usd(price_try: float | None, cfg: Config) -> float:
    if not price_try:
        return 19.99  # fallback placeholder; review before publishing
    usd = price_try * cfg.try_to_usd * cfg.price_multiplier
    # round to .99
    return max(0.20, math.floor(usd) + 0.99)


def build_listing_fields(product: dict, seo: dict, cfg: Config) -> dict:
    L = cfg.listing
    price = compute_price_usd(product.get("price_try"), cfg)
    fields = {
        "quantity": int(L.get("quantity", 1)),
        "title": seo["title"],
        "description": seo["description"],
        "price": round(price, 2),
        "who_made": L.get("who_made", "someone_else"),
        "when_made": L.get("when_made", "2020_2026"),
        "taxonomy_id": int(L["taxonomy_id"]),
        "is_supply": bool(L.get("is_supply", False)),
        "should_auto_renew": False,
        "type": "physical",
        "state": L.get("state", "draft"),
        "tags": seo.get("tags", [])[:13],
        "materials": (seo.get("materials") or L.get("materials") or [])[:5],
    }
    for opt in ("shipping_profile_id", "return_policy_id", "shop_section_id"):
        if L.get(opt):
            fields[opt] = int(L[opt])
    return fields


def process_one(url: str, shop_id: int, client: EtsyClient, cfg: Config) -> int:
    product = scrape_product(url, delay=cfg.source.get("request_delay_seconds", 1.0)).to_dict()
    if not product["title"]:
        raise RuntimeError("Could not scrape a title")

    seo = optimize(product, cfg)
    fields = build_listing_fields(product, seo, cfg)

    # gather images
    icfg = cfg.images
    image_paths: list[str] = []
    if icfg.get("use_supplier_images", True):
        image_paths += imgmod.download_supplier_images(
            product, icfg.get("max_supplier_images", 6))
    if icfg.get("generate_lifestyle", False) and cfg.openai_api_key:
        ref = image_paths[0] if image_paths else None
        image_paths += imgmod.generate_lifestyle_images(
            product, ref, icfg.get("lifestyle_count", 2),
            cfg.openai_api_key, icfg.get("lifestyle_model", "gpt-image-1"))
    image_paths = image_paths[:20]  # Etsy max

    if cfg.etsy.get("dry_run"):
        print(f"  [dry-run] would create draft '{fields['title'][:50]}...' "
              f"price=${fields['price']} imgs={len(image_paths)} tags={len(fields['tags'])}")
        return -1

    created = client.create_draft_listing(shop_id, fields)
    listing_id = created["listing_id"]

    for rank, path in enumerate(image_paths, start=1):
        try:
            client.upload_listing_image(shop_id, listing_id, path, rank=rank,
                                        alt_text=fields["title"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! image upload failed (rank {rank}): {e}")

    store.mark_done(url, listing_id, {"product": product, "seo": seo, "fields": fields})
    return listing_id


def run_batch(cfg: Config, shop_id: int, limit: int | None = None):
    store.init()
    client = EtsyClient(cfg.etsy_api_key,
                        requests_per_second=cfg.etsy.get("requests_per_second", 5))
    urls = store.pending(limit)
    print(f"Processing {len(urls)} product(s). dry_run={cfg.etsy.get('dry_run')}")
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}")
        try:
            lid = process_one(url, shop_id, client, cfg)
            if lid != -1:
                print(f"  -> draft listing {lid}")
        except Exception as e:  # noqa: BLE001
            store.mark_error(url, traceback.format_exc())
            print(f"  ! ERROR: {e}")
    print("Done. Counts:", store.counts())
