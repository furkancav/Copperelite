#!/usr/bin/env python3
"""Web UI server — CuLister Etsy AI Lister."""
from __future__ import annotations
import json, os, sys, threading, queue, time, uuid, shutil, subprocess
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env", override=True)
sys.path.insert(0, str(ROOT))

from flask import Flask, render_template, request, jsonify, Response, send_from_directory
import create_listing as cl
from etsy_lister.etsy_client import EtsyClient

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

# ── Basit kullanıcı adı + şifre koruması ──────────────────────────────────────
# Render'da APP_PASSWORD ayarlanınca aktif olur. Boşsa (local) koruma kapalıdır.
# Kullanıcı adı APP_USERNAME ile değiştirilebilir; verilmezse "copper" olur.
APP_USERNAME = os.getenv("APP_USERNAME", "copper")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")


@app.before_request
def _require_password():
    if not APP_PASSWORD:
        return  # koruma kapalı
    if request.endpoint == "static" or request.path.startswith("/static/"):
        return  # üretilen görseller (uuid path zaten tahmin edilemez)
    auth = request.authorization
    if not auth or auth.username != APP_USERNAME or auth.password != APP_PASSWORD:
        # realm sadece ASCII olmalı (HTTP header kuralı)
        return Response(
            "Giris gerekli", 401,
            {"WWW-Authenticate": 'Basic realm="CuLister"'},
        )

UPLOADS = ROOT / "uploads"
STATIC_JOBS = ROOT / "static" / "jobs"
UPLOADS.mkdir(exist_ok=True)
STATIC_JOBS.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}


# ── SSE stream ────────────────────────────────────────────────────────────────

def _stream(job_id: str):
    q: queue.Queue = JOBS[job_id]["q"]
    while True:
        try:
            msg = q.get(timeout=30)
            yield f"data: {msg}\n\n"
            if json.loads(msg).get("type") in ("done", "error"):
                break
        except queue.Empty:
            yield 'data: {"type":"ping"}\n\n'


# ── Background job ────────────────────────────────────────────────────────────

def _run(job_id: str, priced_sizes: list[dict], section_id: int):
    job = JOBS[job_id]
    q: queue.Queue = job["q"]
    info = job["info"]
    upload_path = job["upload_path"]
    out_dir = STATIC_JOBS / job_id
    base_price = min(ps["price"] for ps in priced_sizes)

    def push(**kw):
        q.put(json.dumps(kw))

    def on_image(i, total, scene_type, path):
        dest = out_dir / Path(path).name
        if not dest.exists():
            shutil.copy2(path, dest)
        push(type="image", index=i, total=total, scene=scene_type,
             url=f"/static/jobs/{job_id}/{Path(path).name}")

    try:
        push(type="step", msg="Görseller üretiliyor...", progress=5)
        images = cl.generate_images(info, upload_path, out_dir, on_image=on_image)

        if not images:
            push(type="error", message="Görsel üretilemedi. fal.ai kredinizi kontrol edin.")
            return

        push(type="step", msg="SEO başlık ve açıklama yazılıyor...", progress=78)
        content = cl.generate_content(info, [ps["label"] for ps in priced_sizes])

        push(type="step", msg="Kargo profili kontrol ediliyor...", progress=82)
        client = EtsyClient(cl.ETSY_KEY, cl.ETSY_SECRET)
        shipping_id = cl.ensure_free_shipping_profile(client)

        push(type="step", msg="Etsy listing oluşturuluyor...", progress=86)
        fields = {
            "title": content["title"][:140],
            "description": content["description"],
            "price": base_price,
            "quantity": 10,
            "who_made": "i_did",
            "when_made": "made_to_order",
            "is_supply": False,
            "taxonomy_id": 11353,
            "shipping_profile_id": shipping_id,
            "return_policy_id": cl.RETURN_POLICY_ID,
            "shop_section_id": section_id,
            "tags": cl.clean_tags(content["tags"]),
            "materials": ["Copper"],
            "state": "draft",
            "readiness_state_id": cl.READINESS_STATE,
            "should_auto_renew": True,
            "is_customizable": True,
        }
        listing = client.create_draft_listing(cl.SHOP_ID, fields)
        lid = listing["listing_id"]

        push(type="step", msg="Görseller Etsy'ye yükleniyor...", progress=90)
        image_url = ""
        for rank, img_path in enumerate(images, 1):
            resp = client.upload_listing_image(cl.SHOP_ID, lid, str(img_path), rank=rank)
            if rank == 1 and isinstance(resp, dict):
                image_url = resp.get("url_fullxfull") or resp.get("url_570xN") or ""
            time.sleep(0.4)

        push(type="step", msg="Ölçü/fiyat varyasyonları ekleniyor...", progress=96)
        cl.add_size_variations(client, lid, priced_sizes)

        # Kârlılık tablosuna (Google Sheet) her varyasyonu işle
        push(type="step", msg="Kârlılık tablosuna işleniyor...", progress=98)
        try:
            cl.push_to_sheet(lid, content["title"], _section_name(section_id),
                             image_url, f"https://www.etsy.com/listing/{lid}", priced_sizes)
        except Exception as e:
            print("Sheet push hatası:", e)

        # Listing TASLAK olarak kalır — publish EDİLMEZ.
        push(type="done",
             url=f"https://www.etsy.com/your/shops/me/tools/listings/{lid}",
             listing_id=lid,
             title=content["title"])

    except Exception as e:
        push(type="error", message=str(e))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sections = [{"id": s[1], "name": s[0]} for s in cl.SECTIONS]
    return render_template("index.html", sections=json.dumps(sections),
                           max_images=max(1, cl.MAX_IMAGES))


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "image" not in request.files:
        return jsonify(ok=False, error="Görsel seçilmedi."), 400
    f = request.files["image"]
    ext = Path(f.filename).suffix.lower() or ".jpg"
    job_id = uuid.uuid4().hex[:8]
    raw_path = UPLOADS / f"{job_id}{ext}"
    f.save(str(raw_path))

    try:
        info = cl.analyze_image(str(raw_path))
    except Exception as e:
        raw_path.unlink(missing_ok=True)
        return jsonify(ok=False, error=str(e)), 500

    job_dir = STATIC_JOBS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Serve preview as JPEG (browsers can't always show AVIF/HEIC)
    if ext in (".avif", ".webp", ".heic", ".heif"):
        jpg_path, _ = cl._to_jpeg_if_needed(str(raw_path))
        preview_jpg = job_dir / "preview.jpg"
        shutil.move(jpg_path, str(preview_jpg))
        preview_name = "preview.jpg"
    else:
        shutil.copy2(str(raw_path), str(job_dir / f"preview{ext}"))
        preview_name = f"preview{ext}"

    # Görselden ürün oranını + ABD popüler ölçüleri (kullanıcı her biri için fiyat girecek)
    sizes = cl.suggest_sizes(info, str(raw_path))

    JOBS[job_id] = {
        "q": queue.Queue(),
        "info": info,
        "upload_path": str(raw_path),
        "sizes": sizes,
    }

    return jsonify(ok=True, job_id=job_id, info=info, sizes=sizes,
                   preview_url=f"/static/jobs/{job_id}/{preview_name}")


def _section_name(section_id: int) -> str:
    for name, sid in cl.SECTIONS:
        if sid == section_id:
            return name
    return ""


def _cost_to_prices(job_id: str, section_id: int, items: list) -> list[dict]:
    """[{label, cost}] → [{label, price, desi, shipping}] (maliyetten hedef fiyat)."""
    section_name = _section_name(section_id)
    meta = {s["label"]: s for s in JOBS.get(job_id, {}).get("sizes", [])}
    out = []
    for s in items or []:
        label = str(s.get("label", "")).strip()
        try:
            cost = float(s.get("cost", 0) or 0)
        except (ValueError, TypeError):
            cost = 0
        m = meta.get(label)
        if label and cost > 0 and m:
            w_cm, h_cm = m.get("w_cm", 0), m.get("h_cm", 0)
            en, boy, yuk = cl.derive_dimensions(section_name, w_cm, h_cm)
            r = cl.price_for_size(cost, section_name, w_cm, h_cm)
            out.append({"label": label, "price": r["price"],
                        "desi": r["desi"], "shipping": r["shipping"],
                        "cost": cost, "en": en, "boy": boy, "yuk": yuk})
    return out


@app.route("/api/price", methods=["POST"])
def api_price():
    """Maliyet → hedef fiyat önizlemesi (canlı; listing oluşturmaz)."""
    data = request.json or {}
    job_id = data.get("job_id", "")
    if job_id not in JOBS:
        return jsonify(ok=False, error="Geçersiz iş."), 400
    prices = _cost_to_prices(job_id, int(data.get("section_id", 0) or 0), data.get("sizes"))
    return jsonify(ok=True, prices=prices)


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.json or {}
    job_id = data.get("job_id", "")
    section_id = int(data.get("section_id", 0) or 0)

    if job_id not in JOBS:
        return jsonify(ok=False, error="Geçersiz iş."), 400

    # sizes: [{"label": str, "cost": float}, ...] — maliyetten fiyat + boyut hesaplanır
    priced = _cost_to_prices(job_id, section_id, data.get("sizes"))
    if not priced:
        return jsonify(ok=False, error="En az bir ölçü için geçerli maliyet girin."), 400

    threading.Thread(target=_run, args=(job_id, priced, section_id), daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/stream/<job_id>")
def api_stream(job_id):
    if job_id not in JOBS:
        return "Not found", 404
    r = Response(_stream(job_id), mimetype="text/event-stream")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["X-Accel-Buffering"] = "no"
    return r


if __name__ == "__main__":
    import webbrowser
    port = int(os.getenv("PORT", "5050"))
    url = f"http://localhost:{port}"
    print(f"▶  {url}  adresinde başlatılıyor...")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
