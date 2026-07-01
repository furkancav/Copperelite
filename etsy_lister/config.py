"""Load environment + YAML config into a single object."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass
class Config:
    raw: dict
    # secrets
    etsy_api_key: str
    etsy_api_secret: str
    etsy_redirect_uri: str
    openai_api_key: str
    gemini_api_key: str
    try_to_usd: float
    price_multiplier: float

    def section(self, name: str) -> dict:
        return self.raw.get(name, {}) or {}

    @property
    def listing(self) -> dict:
        return self.section("listing")

    @property
    def content(self) -> dict:
        return self.section("content")

    @property
    def images(self) -> dict:
        return self.section("images")

    @property
    def source(self) -> dict:
        return self.section("source")

    @property
    def etsy(self) -> dict:
        return self.section("etsy")


def load_config(config_path: str | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_file = Path(config_path) if config_path else ROOT / "config.yaml"
    raw = {}
    if cfg_file.exists():
        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    return Config(
        raw=raw,
        etsy_api_key=os.getenv("ETSY_API_KEY", ""),
        etsy_api_secret=os.getenv("ETSY_API_SECRET", ""),
        etsy_redirect_uri=os.getenv("ETSY_REDIRECT_URI", "http://localhost:3003/callback"),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        try_to_usd=float(os.getenv("TRY_TO_USD", "0.030")),
        price_multiplier=float(os.getenv("PRICE_MULTIPLIER", "2.2")),
    )
