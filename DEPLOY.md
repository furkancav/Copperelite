# CuLister — Render'a Ücretsiz Deploy Rehberi

Bu uygulamayı Render.com üzerinden ortaklarınla paylaşmak için adım adım rehber.
Toplam süre: ~15 dakika.

---

## ⚠️ Önce bilmen gerekenler

- **Hosting ücretsiz**, ama her listing üretimi senin **fal.ai** ve **Gemini**
  hesabından kredi harcar. Ortakların her kullanımı senin faturana yazılır.
- Uygulama **şifreyle** korunuyor. Şifreyi sadece güvendiğin ortaklarına ver.
- Render ücretsiz plan: 15 dakika kullanılmazsa uyur, ilk açılışta ~1 dakika
  gecikir. Sonra normal hızda çalışır.

---

## Adım 1 — Kodu GitHub'a yükle

Uygulama zaten git deposu olarak hazırlandı (gizli anahtarların `.gitignore` ile
korundu). GitHub'a göndermek için:

1. https://github.com/new adresinden **boş** bir repo oluştur (örn. `culister`).
   README/gitignore ekleme, boş bırak.
2. Proje klasöründe şu komutları çalıştır (GITHUB_KULLANICI_ADIN'ı değiştir):

```bash
# İlk kez git kullanıyorsan kimliğini ayarla (bir kez yeterli):
git config user.email "furkancav@gmail.com"
git config user.name "Furkan"

# Kodu commit'le ve GitHub'a gönder:
git commit -m "CuLister web app"
git branch -M main
git remote add origin https://github.com/GITHUB_KULLANICI_ADIN/culister.git
git push -u origin main
```

> 🔑 `git push` sırasında GitHub kullanıcı adı + şifre sorabilir. GitHub artık normal
> şifre kabul etmiyor; şifre yerine **Personal Access Token** gerekir
> (github.com → Settings → Developer settings → Personal access tokens → repo izniyle
> oluştur, şifre alanına onu yapıştır). En kolayı: GitHub Desktop uygulamasıyla push.

> 🔒 `.env` ve `data/etsy_token.json` GitHub'a **gitmez** (gitignore ile korunuyor).
> Doğrulamak için: `git status` çıktısında bu dosyalar görünmemeli.

---

## Adım 2 — Render'da servisi oluştur

1. https://render.com adresine gir, GitHub ile ücretsiz kayıt ol.
2. **New +** → **Blueprint** seç.
3. Az önce oluşturduğun `culister` reposunu bağla.
4. Render `render.yaml` dosyasını otomatik okur ve servisi kurar.

---

## Adım 3 — Gizli anahtarları gir (EN ÖNEMLİ ADIM)

Render, `render.yaml`'daki `sync: false` anahtarlarını senden isteyecek.
Servis sayfasında **Environment** sekmesinden şu 6 değeri gir:

| Anahtar            | Değer                                                        |
|--------------------|--------------------------------------------------------------|
| `ETSY_API_KEY`     | Etsy keystring'in                                            |
| `ETSY_API_SECRET`  | Etsy shared secret'ın                                        |
| `ETSY_TOKEN_JSON`  | `data/etsy_token.json` dosyasının **tek satır** içeriği (bkz. Adım 4) |
| `GEMINI_API_KEY`   | Google AI Studio anahtarın                                   |
| `FAL_KEY`          | fal.ai anahtarın                                             |
| `APP_PASSWORD`     | Ortaklarına vereceğin şifre (kendin belirle)                 |

---

## Adım 4 — Etsy token'ını hazırla

Bulutta `python run.py auth` çalıştıramazsın, o yüzden local'de oluşan token'ı
taşıman gerekir. Proje klasöründe şunu çalıştır:

```bash
python -c "print(open('data/etsy_token.json').read().replace(chr(10),''))"
```

Çıkan tek satırlık JSON'u kopyala ve Render'da `ETSY_TOKEN_JSON` değeri olarak yapıştır.

> Not: Etsy token'ı ~90 günde bir yenilenmeli. Süre dolarsa local'de
> `python run.py auth` yapıp bu adımı tekrarla.

---

## Adım 5 — Deploy et ve paylaş

1. **Manual Deploy** → **Deploy latest commit** (veya otomatik başlar).
2. Build bitince Render sana bir adres verir:
   `https://culister.onrender.com`
3. Bu adresi + şifreyi (`APP_PASSWORD`) ortaklarınla paylaş.
4. Ortakların adrese girer, şifreyi yazar, fotoğraf yükler → listing hazır.

---

## Sorun giderme

- **"Yetkilendirme yok" hatası** → `ETSY_TOKEN_JSON` eksik/yanlış. Adım 4'ü tekrarla.
- **Görsel üretilmiyor** → `FAL_KEY` yanlış veya fal.ai kredin bitmiş.
- **Şifre sorulmuyor** → `APP_PASSWORD` boş kalmış. Ekleyip yeniden deploy et.
- **Çok yavaş açılıyor** → ücretsiz plan uykudan uyanıyor, ~1 dk normal.
