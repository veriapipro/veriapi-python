# VeriApi.Pro Python SDK: döviz kuru, namaz vakti, hava durumu ve deprem API'si

Python ile döviz kurlarını, namaz vakitlerini, hava durumunu, son depremleri ve il/ilçe listesini tek API anahtarıyla çekin.

Official Python client for [VeriApi.Pro](https://veriapi.pro/?ref=github): Turkish exchange rates, prayer times, weather, earthquakes and cities.

Python 3.9+, bağımlılık yok.

```bash
pip install veriapi
```

## Hızlı başlangıç

```python
from veriapi import VeriApi, VeriApiHatasi

api = VeriApi("va_ANAHTARINIZ")

sonuc = api.doviz("USD,EUR")
print(sonuc.veri["kurlar"])
print(sonuc.kaynak[0]["atif"])  # veriyi gösterdiğiniz yerde görünür yazın
print(sonuc.guncelleme, sonuc.bayat)
print(sonuc.kota.gunluk_kalan, sonuc.kota.dakikalik_kalan, sonuc.kota.istek_maliyeti)
```

## Yöntemler

```python
api.doviz(["USD", "EUR"], tur="referans")      # tur: anlik | referans
api.hava(il="duzce", ilce="akcakoca")          # ya da konum="duzce-akcakoca", ya da enlem=, boylam=
api.namaz(il="ankara", tarih="2026-11-05", gun=7)
api.depremler(limit=10, min_buyukluk=4.0, saat=24, il="izmir", yurtici=True)
api.iller()
api.ilceler("duzce")                           # ad, slug ya da plaka

api.get("/v1/takvim/tatiller", {"yil": 2026})  # diğer tüm uçlar
```

## Seçenekler

```python
api = VeriApi(
    "va_ANAHTARINIZ",
    zaman_asimi=10.0,    # istek başına saniye
    yeniden_deneme=2,    # 429'da en çok kaç kez daha; 0 kapatır
    azami_bekleme=60.0,  # bundan uzun Retry-After beklenmez, hata hemen fırlatılır
)
```

## Hatalar

```python
try:
    api.namaz(il="duzce", gun=99)
except VeriApiHatasi as hata:
    print(hata.kod, hata.mesaj, hata.http_durumu)
    print(hata.alanlar)      # 422: [{"alan": "gun", "mesaj": "..."}]
    print(hata.retry_after)  # 429: saniye
    print(hata.zaman_asimi)  # zaman aşımında True; bağlantı hatasında kod ve http_durumu 0
```

## Webhook doğrulama

Ham gövdeyi kullanın; JSON'u ayrıştırıp yeniden üretirseniz imza tutmaz.

```python
from flask import Flask, abort, request
from veriapi import imza_dogrula

app = Flask(__name__)

@app.post("/webhook")
def webhook():
    gecerli = imza_dogrula(
        request.get_data(),
        request.headers.get("X-VeriApi-Imza"),
        request.headers.get("X-VeriApi-Zaman"),
        "WEBHOOK_GIZLI_ANAHTARINIZ",
    )
    if not gecerli:
        abort(401)
    olay = request.headers.get("X-VeriApi-Olay")
    print(olay, request.get_json())
    return "", 204
```

`tolerans=300` saniyeden eski ya da ileri tarihli imza reddedilir; `tolerans=0` kapatır.

## Test

```bash
python3 -m unittest discover -s tests
```

## Bağlantılar

- Ücretsiz anahtar ve canlı deneme: <https://veriapi.pro/?ref=github>
- Dokümantasyon: <https://veriapi.pro/dokumantasyon>
- OpenAPI: <https://api.veriapi.pro/v1/openapi.json>

Lisans: MIT
