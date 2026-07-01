"""Etsy OAuth 2.0 (PKCE) — manual-paste flow + automatic token refresh.

Etsy does NOT allow localhost/IP callback URLs, so we use a registered domain
callback (e.g. https://example.com/callback) and let the user copy the `code`
from the browser address bar after consenting. No local server needed.

Docs: https://developers.etsy.com/documentation/essentials/authentication
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import webbrowser

import requests

from .config import DATA_DIR

AUTH_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
SCOPES = ["listings_r", "listings_w", "shops_r", "shops_w"]
TOKEN_FILE = DATA_DIR / "etsy_token.json"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _extract_code_state(pasted: str) -> tuple[str, str | None]:
    pasted = pasted.strip()
    if pasted.startswith("http"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        return qs.get("code", [""])[0], qs.get("state", [None])[0]
    # user may paste just the code
    return pasted, None


def authorize(api_key: str, redirect_uri: str, api_secret: str = "") -> dict:
    """One-time consent. Opens the browser, you approve, then paste the redirected URL."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": api_key,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("\n1) Your browser will open the Etsy authorization page.")
    print("2) Click 'Allow Access'.")
    print(f"3) You'll be redirected to {redirect_uri}?code=...  (the page itself may be blank).")
    print("4) COPY THE FULL URL from the address bar and paste it below.\n")
    print("If the browser doesn't open, paste this URL manually:\n", url, "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    pasted = input("Paste the redirected URL (or just the code): ").strip()
    code, returned_state = _extract_code_state(pasted)
    if not code:
        raise RuntimeError("No code found in what you pasted.")
    if returned_state and returned_state != state:
        raise RuntimeError("State mismatch — aborting for safety. Try `auth` again.")

    post_data = {
        "grant_type": "authorization_code",
        "client_id": api_key,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": verifier,
    }
    if api_secret:
        post_data["client_secret"] = api_secret
    resp = requests.post(TOKEN_URL, data=post_data, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Token exchange failed {resp.status_code}: {resp.text}")
    tok = resp.json()
    tok["obtained_at"] = int(time.time())
    TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    print("\nAuthorized. Token saved to", TOKEN_FILE)
    return tok


def _load_token() -> dict | None:
    """Token'ı önce dosyadan, yoksa ETSY_TOKEN_JSON env değişkeninden oku.
    Render gibi ortamlarda dosya olmadığı için env fallback gerekir."""
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    env_tok = os.getenv("ETSY_TOKEN_JSON", "").strip()
    if env_tok:
        try:
            return json.loads(env_tok)
        except json.JSONDecodeError:
            return None
    return None


def _save_token(tok: dict) -> None:
    """Token'ı dosyaya yaz. Read-only dosya sisteminde (bulut) sessizce geç."""
    try:
        TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    except OSError:
        pass


def _refresh(api_key: str, refresh_token: str, api_secret: str = "") -> dict:
    post_data = {
        "grant_type": "refresh_token",
        "client_id": api_key,
        "refresh_token": refresh_token,
    }
    if api_secret:
        post_data["client_secret"] = api_secret
    resp = requests.post(TOKEN_URL, data=post_data, timeout=30)
    resp.raise_for_status()
    tok = resp.json()
    tok["obtained_at"] = int(time.time())
    tok.setdefault("refresh_token", refresh_token)
    _save_token(tok)
    return tok


def get_access_token(api_key: str, api_secret: str = "") -> str:
    tok = _load_token()
    if not tok:
        raise RuntimeError(
            "Yetkilendirme yok. Local'de:  python run.py auth  "
            "| Bulutta: ETSY_TOKEN_JSON environment variable'ını ayarla."
        )
    age = int(time.time()) - tok.get("obtained_at", 0)
    if age >= tok.get("expires_in", 3600) - 120:
        tok = _refresh(api_key, tok["refresh_token"], api_secret)
    return tok["access_token"]


def user_id_from_token() -> str | None:
    tok = _load_token()
    if not tok:
        return None
    return tok["access_token"].split(".")[0]
