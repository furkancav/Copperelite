"""Thin Etsy Open API v3 client: auth headers, rate limiting, the calls we need."""
from __future__ import annotations
import time
from pathlib import Path

import requests

from . import etsy_auth

BASE = "https://api.etsy.com/v3/application"


class EtsyClient:
    def __init__(self, api_key: str, api_secret: str = "", requests_per_second: float = 5.0):
        self.api_key = api_key
        self.api_secret = api_secret
        self._min_interval = 1.0 / max(requests_per_second, 0.5)
        self._last = 0.0
        self.session = requests.Session()

    # ---- internals ----
    def _throttle(self):
        gap = time.time() - self._last
        if gap < self._min_interval:
            time.sleep(self._min_interval - gap)
        self._last = time.time()

    def _headers(self, json_accept: bool = True) -> dict:
        api_key_header = f"{self.api_key}:{self.api_secret}" if self.api_secret else self.api_key
        h = {
            "x-api-key": api_key_header,
            "Authorization": f"Bearer {etsy_auth.get_access_token(self.api_key, self.api_secret)}",
        }
        if json_accept:
            h["Accept"] = "application/json"
        return h

    def _request(self, method: str, path: str, *, retries: int = 4, **kwargs):
        url = path if path.startswith("http") else f"{BASE}{path}"
        for attempt in range(retries):
            self._throttle()
            resp = self.session.request(method, url, headers=self._headers(
                json_accept="files" not in kwargs), timeout=60, **kwargs)
            if resp.status_code == 429:  # rate limited
                wait = int(resp.headers.get("Retry-After", 2)) + attempt
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if not resp.ok:
                raise EtsyError(resp.status_code, resp.text)
            return resp.json() if resp.content else {}
        raise EtsyError(resp.status_code, resp.text)

    # ---- read helpers ----
    def get_me(self) -> dict:
        return self._request("GET", "/users/me")

    def get_shops_for_user(self, user_id: str | int) -> dict:
        return self._request("GET", f"/users/{user_id}/shops")

    def get_shipping_profiles(self, shop_id: int) -> dict:
        return self._request("GET", f"/shops/{shop_id}/shipping-profiles")

    def get_return_policies(self, shop_id: int) -> dict:
        return self._request("GET", f"/shops/{shop_id}/policies/return")

    def get_sections(self, shop_id: int) -> dict:
        return self._request("GET", f"/shops/{shop_id}/sections")

    def get_seller_taxonomy(self) -> dict:
        return self._request("GET", "/seller-taxonomy/nodes")

    # ---- write ----
    def create_draft_listing(self, shop_id: int, fields: dict) -> dict:
        """fields: dict matching createDraftListing form params.
        Arrays (tags, materials) are sent as repeated keys via requests `data` list-of-tuples.
        """
        data = []
        for k, v in fields.items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    data.append((f"{k}[]", item))
            elif v is not None:
                data.append((k, v))
        return self._request("POST", f"/shops/{shop_id}/listings", data=data)

    def upload_listing_image(self, shop_id: int, listing_id: int, image_path: str,
                             rank: int = 1, alt_text: str = "") -> dict:
        p = Path(image_path)
        with p.open("rb") as fh:
            files = {"image": (p.name, fh, "image/jpeg")}
            data = {"rank": str(rank)}
            if alt_text:
                data["alt_text"] = alt_text[:500]
            return self._request(
                "POST",
                f"/shops/{shop_id}/listings/{listing_id}/images",
                files=files, data=data,
            )


    def publish_listing(self, shop_id: int, listing_id: int) -> dict:
        return self._request("PATCH", f"/shops/{shop_id}/listings/{listing_id}",
                             json={"state": "active"})


class EtsyError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Etsy API {status}: {body}")
        self.status = status
        self.body = body
