"""Turn raw (Turkish) product data into Etsy-ready English SEO content.

Returns a dict: {title, description, tags[<=13], materials[]}.
Uses OpenAI by default; Gemini optional.
"""
from __future__ import annotations
import json
import re

import requests

SYSTEM = (
    "You are an Etsy SEO copywriter. Given a raw product (often Turkish), produce "
    "high-converting ENGLISH listing content for the US Etsy market. Respond with "
    "STRICT JSON only, no markdown."
)

PROMPT_TMPL = """Raw product data:
TITLE: {title}
DESCRIPTION: {description}

Produce JSON with these keys:
- "title": compelling, keyword-rich English title, MAX {title_max} characters, front-load the most-searched keywords, no ALL CAPS, use commas to separate concepts.
- "description": 120-220 word English description. First two sentences must hook + include main keywords. Then a short bulleted spec section using the source facts (sizes, material, bulb type, etc.). Do not invent specs that aren't in the source.
- "tags": EXACTLY {tags_count} English search tags, each MAX 20 characters, multi-word phrases buyers actually search, no duplicates, no '#'.
- "materials": up to 5 short English material words (e.g. wood, metal).

Only output JSON."""


def _clean_tags(tags: list[str], n: int) -> list[str]:
    out, seen = [], set()
    for t in tags:
        t = re.sub(r"[^A-Za-z0-9 '\-]", "", str(t)).strip().lower()
        if t and len(t) <= 20 and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= n:
            break
    return out


def _via_openai(api_key: str, prompt: str, model: str = "gpt-5.4") -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    return json.loads(resp.choices[0].message.content)


def _via_gemini(api_key: str, prompt: str, model: str = "gemini-flash-latest") -> dict:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def optimize(product: dict, cfg) -> dict:
    content = cfg.content
    prompt = PROMPT_TMPL.format(
        title=product.get("title", ""),
        description=product.get("description", "")[:2000],
        title_max=content.get("title_max", 140),
        tags_count=content.get("tags_count", 13),
    )
    provider = content.get("provider", "openai")
    if provider == "gemini":
        data = _via_gemini(cfg.gemini_api_key, prompt)
    else:
        data = _via_openai(cfg.openai_api_key, prompt)

    title = str(data.get("title", "")).strip()[: content.get("title_max", 140)]
    tags = _clean_tags(data.get("tags", []), content.get("tags_count", 13))
    materials = [re.sub(r"[^A-Za-z0-9 ]", "", str(m)).strip()
                 for m in data.get("materials", [])][:5]
    return {
        "title": title,
        "description": str(data.get("description", "")).strip(),
        "tags": tags,
        "materials": [m for m in materials if m],
    }
