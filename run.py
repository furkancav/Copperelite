#!/usr/bin/env python3
"""CLI for the Etsy bulk lister.

Typical first-time flow:
    python run.py auth                          # one-time Etsy authorization
    python run.py shopinfo                      # find shop_id, shipping_profile_id, etc.
    python run.py taxonomy --search "pendant"   # find taxonomy_id, put it in config.yaml
    python run.py crawl --category "https://www.bamyum.com/avize-sarkit"
    python run.py crawl --urls-file urls.txt    # (alternative) one product URL per line
    python run.py run --shop-id 12345678 --limit 1   # test with ONE product first
    python run.py status
"""
from __future__ import annotations
import argparse
import sys

from etsy_lister import store
from etsy_lister.config import load_config
from etsy_lister import etsy_auth
from etsy_lister.etsy_client import EtsyClient
from etsy_lister.scraper import collect_product_urls
from etsy_lister.pipeline import run_batch


def _flatten_taxonomy(nodes, trail=""):
    for n in nodes:
        name = f"{trail} > {n['name']}" if trail else n["name"]
        yield n["id"], name
        for child in n.get("children", []) or []:
            yield from _flatten_taxonomy([child], name)


def cmd_auth(cfg, args):
    if not cfg.etsy_api_key:
        sys.exit("Set ETSY_API_KEY in .env first.")
    etsy_auth.authorize(cfg.etsy_api_key, cfg.etsy_redirect_uri, cfg.etsy_api_secret)


def cmd_shopinfo(cfg, args):
    client = EtsyClient(cfg.etsy_api_key, cfg.etsy_api_secret)
    me = client.get_me()
    uid = me.get("user_id") or etsy_auth.user_id_from_token()
    print("user_id:", uid)
    shops = client.get_shops_for_user(uid)
    results = shops.get("results", shops if isinstance(shops, list) else [shops])
    for s in (results if isinstance(results, list) else [results]):
        sid = s.get("shop_id")
        print(f"\nSHOP: {s.get('shop_name')}  shop_id={sid}")
        try:
            for sp in client.get_shipping_profiles(sid).get("results", []):
                print(f"  shipping_profile_id={sp['shipping_profile_id']}  {sp.get('title')}")
        except Exception as e:
            print("  (shipping profiles)", e)
        try:
            for rp in client.get_return_policies(sid).get("results", []):
                print(f"  return_policy_id={rp['return_policy_id']}  {rp.get('return_deadline')}d")
        except Exception as e:
            print("  (return policies)", e)
        try:
            for sec in client.get_sections(sid).get("results", []):
                print(f"  shop_section_id={sec['shop_section_id']}  {sec.get('title')}")
        except Exception as e:
            print("  (sections)", e)


def cmd_taxonomy(cfg, args):
    client = EtsyClient(cfg.etsy_api_key, cfg.etsy_api_secret)
    tax = client.get_seller_taxonomy().get("results", [])
    q = (args.search or "").lower()
    for tid, name in _flatten_taxonomy(tax):
        if not q or q in name.lower():
            print(f"{tid}\t{name}")


def cmd_crawl(cfg, args):
    store.init()
    if args.urls_file:
        urls = [l.strip() for l in open(args.urls_file, encoding="utf-8") if l.strip()]
    elif args.category:
        print("Crawling category...")
        urls = collect_product_urls(args.category, args.max_pages,
                                    cfg.source.get("request_delay_seconds", 1.0))
    else:
        sys.exit("Provide --category URL or --urls-file path")
    store.add_urls(urls)
    print(f"Added {len(urls)} URLs. Totals: {store.counts()}")


def cmd_run(cfg, args):
    if not args.shop_id:
        sys.exit("Pass --shop-id (see `python run.py shopinfo`)")
    if args.dry_run:
        cfg.raw.setdefault("etsy", {})["dry_run"] = True
    run_batch(cfg, int(args.shop_id), args.limit)


def cmd_status(cfg, args):
    store.init()
    print(store.counts())


def main():
    ap = argparse.ArgumentParser(description="Etsy bulk lister")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth")
    sub.add_parser("shopinfo")
    t = sub.add_parser("taxonomy"); t.add_argument("--search", default="")
    c = sub.add_parser("crawl")
    c.add_argument("--category"); c.add_argument("--urls-file")
    c.add_argument("--max-pages", type=int, default=50)
    r = sub.add_parser("run")
    r.add_argument("--shop-id"); r.add_argument("--limit", type=int)
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")

    args = ap.parse_args()
    cfg = load_config(args.config)
    {
        "auth": cmd_auth, "shopinfo": cmd_shopinfo, "taxonomy": cmd_taxonomy,
        "crawl": cmd_crawl, "run": cmd_run, "status": cmd_status,
    }[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
