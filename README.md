# face-auth

Projelerine takılabilen **yüz doğrulama ve canlılık testi** eklentisi. Kritik işlemlerden (para çekme, API anahtarı ekleme, ayar değiştirme) önce kullanıcının gerçekten o kişi olduğunu ve kameranın karşısında canlı olarak durduğunu doğrular.

> Bu modül şifrenin yerine geçmez; ona ek bir katmandır (step-up authentication). Normal giriş şifre ve 2FA ile yapılır, yüz doğrulaması yalnızca riskli işlemlerde istenir.

## Nasıl çalışır

```
 Senin projen (backend)          face-auth servisi              Tarayıcı (faceauth.js)
 ─────────────────────           ─────────────────              ──────────────────────
 1. POST /v1/challenges  ───────►  rastgele hareket dizisi
    (X-API-Key)            ◄───────  {challenge_id, actions}
 2. challenge'ı tarayıcıya ver ─────────────────────────────►  kamera açılır, her hareket
                                                               için bir kare çekilir
                                   3. POST /complete ◄────────  kareler
                                   canlılık + yüz eşleşmesi
                                   ───────────────────────────►  {token}  (5 dk, tek kullanımlık)
 4. kritik istek + X-Face-Token ◄───────────────────────────────
    token'ı public key ile doğrula (JWKS) → işlemi yap
```

- **Yüz eşleştirme:** OpenCV Zoo'daki YuNet (yüz bulma ve 5 nokta) ve SFace (yüz embedding'i) modelleri kullanılır. İlk çalıştırmada yaklaşık 38 MB otomatik indirilir. Ağır bir framework gerekmez.
- **Canlılık testi:** Sunucu her seferinde rastgele bir hareket dizisi ister, örneğin "düz bak, sola dön, yukarı bak". Baş pozu kişinin kendi düz bakışına göre ölçülür. Ayrıca tüm karelerde aynı kişinin olduğu ve karede tek bir yüz bulunduğu kontrol edilir.
- **İsteğe bağlı pasif anti-spoofing:** Bir MiniFASNet ONNX modelinin yolunu `FACEAUTH_ANTISPOOF_MODEL` ile verirsen ekran ve basılı fotoğraf tespiti de devreye girer.
- **Gizlilik (KVKK):** Fotoğraf hiçbir zaman saklanmaz. Sadece 128 boyutlu yüz embedding'i tutulur ve diskte Fernet ile şifrelenir. `DELETE /v1/users/{id}` ile kullanıcının verisi tamamen silinir (unutulma hakkı). Her işlem denetim kaydına (audit log) yazılır.
- **Token'lar:** RS256 imzalı JWT kullanılır. Token proje (`aud`), kullanıcı (`sub`) ve işlem (`scope`) ile sınırlıdır, 5 dakika geçerlidir ve tek kullanımlıktır (`jti`). Projelerin face-auth ile gizli bir anahtar paylaşması gerekmez; doğrulama public key ile yapılır.
- **Kötüye kullanım koruması:** Challenge tek kullanımlıktır ve 60 saniyede düşer. Art arda 5 başarısız denemeden sonra kullanıcı 15 dakika kilitlenir.

## Çalıştırma

### En kolayı: Windows exe

[Releases](../../releases) sayfasından ya da [Actions](../../actions) sekmesinde son başarılı "build" çalışmasının "Artifacts" kısmından `face-auth.exe`'yi indir ve çift tıkla. Kurulum gerekmez, yüz modelleri exe'nin içinde geliyor. Tarayıcıda demo sayfası kendiliğinden açılır. Önce "Yüzümü kaydet"e, sonra "Doğrula"ya bas.

Veriler (şifreli yüz şablonları, imza anahtarı ve denetim kaydı) `%APPDATA%\face-auth` klasöründe tutulur. Böylece exe'yi güncellediğinde kayıtlar kaybolmaz.

> Windows SmartScreen "tanınmayan uygulama" uyarısı verebilir, çünkü exe imzalı değil. "Ek bilgi → Yine de çalıştır" ile açabilirsin.

### Kaynaktan (geliştirme için)

Windows'ta **`baslat.bat`**'a çift tıkla. Dosya önce son güncellemeyi çeker (`git pull`), ilk çalıştırmada sanal ortamı kurar, gerekli paketleri kontrol eder ve servisi başlatır. Demo sayfası tarayıcıda kendiliğinden açılır.

Elle çalıştırmak istersen:

```bash
pip install -r requirements.txt
python -m faceauth               # http://localhost:8100/demo açılır
```

### Sunucuda: Docker

```bash
echo FACEAUTH_API_KEYS=crypto:$(python -c "import secrets;print(secrets.token_urlsafe(32))") > .env
docker compose up -d --build
```

### Yeni bir sürüm çıkarmak

GitHub'da **Releases → Draft a new release** yolunu izle, bir etiket ver (örneğin `v0.1.0`) ve **Publish**'e bas. GitHub Actions exe'yi Windows'ta derler, gerçek modellerle test eder ve release'e ekler. `main`'e yapılan her push'ta da testler çalışır; exe ve Docker imajı otomatik olarak derlenir.

Gerçek kullanımda her projeye ayrı bir API anahtarı ver:

```bash
set FACEAUTH_API_KEYS=crypto:uzun-rastgele-anahtar-1,blog:uzun-rastgele-anahtar-2
set FACEAUTH_CORS_ORIGINS=https://kripto.example.com,https://blog.example.com
python -m faceauth --host 0.0.0.0 --port 8100
```

(Linux/macOS'te `set` yerine `export` kullan. Üretim ortamında servisi mutlaka HTTPS arkasında çalıştır.)

## Bir projeye eklemek

**Backend (Python, FastAPI örneği).** `clients/python/faceauth_client.py` dosyasını projene kopyala:

```python
from fastapi import Depends, FastAPI
from faceauth_client import FaceAuthClient, fastapi_dependency

fa = FaceAuthClient("http://localhost:8100", api_key="uzun-rastgele-anahtar-1", project="crypto")
app = FastAPI()

@app.post("/face/challenge")                      # tarayıcı buradan challenge ister
def face_challenge(user=Depends(current_user)):
    return fa.create_challenge(user_id=str(user.id), scope="withdraw")

require_face = fastapi_dependency(fa, "withdraw", get_user_id=lambda req: req.state.user.id)

@app.post("/withdraw")                            # kritik işlem: geçerli yüz token'ı şart
def withdraw(body: WithdrawIn, face=Depends(require_face)):
    ...
```

Flask kullanıyorsan aynı dosyadaki `flask_require_face` decorator'ını kullanabilirsin. Python dışındaki diller için (.NET, Node vb.) herhangi bir JWT kütüphanesi `/.well-known/jwks.json` adresindeki public key ile token'ı doğrulayabilir. Kontrol edilmesi gerekenler: `iss=face-auth`, `aud=<proje>`, `sub=<kullanıcı>`, `scope` ve tek kullanımlık `jti`.

**Frontend:**

```html
<script src="http://localhost:8100/faceauth.js"></script>
<script>
  const challenge = await (await fetch("/face/challenge", {method: "POST"})).json();
  const { token } = await FaceAuth.run({ serviceUrl: "http://localhost:8100", challenge });
  await fetch("/withdraw", { method: "POST", headers: { "X-Face-Token": token, ... }, body });
</script>
```

Kullanıcıyı kaydetmek (enroll) için aynı akışı kullan, sadece `fa.create_enrollment(user_id)` çağır. Kayıt işlemini, kullanıcı şifre ve 2FA ile giriş yapmışken yaptır.

## Testler

```bash
pytest -q
```

14 test var ve bunlar sahte bir yüz motoruyla çalışır: başka bir kişi, sabit fotoğraf, yanlış hareket sırası, karede yüz değişimi, iki yüz, tekrar oynatma, süre aşımı, kilitlenme, şifreli depolama, token'ın proje ve kapsam ile sınırlanması ile HTTP katmanı ve istemci kütüphanesi.

## Bilinen sınırlar ve yapılacaklar

- Eşik değerleri (`MATCH_THRESHOLD`, `YAW_DELTA`, `PITCH_DELTA`) gerçek kamera görüntüleriyle **henüz ayarlanmadı**. İlk denemede kendi yüzünle FAR/FRR ölçüp ayarlanmalı (yol haritasının 7. haftası).
- Hareket tabanlı canlılık testi, gerçek zamanlı bir deepfake'e karşı tek başına yeterli değildir. Kritik ortamlarda pasif anti-spoofing modelini açmak gerekir. İleride hareket ve doku analizi birleştirilebilir.
- Challenge'lar bellekte tutulur, bu yüzden tek bir sunucu süreci varsayılır. Ölçeklemek için Redis'e taşınmalı.
- `clients/python` içindeki tek kullanımlık token kontrolü de bellekte tutulur. Birden fazla süreç varsa Redis veya veritabanına taşınmalı.
