"""Sahte yerel HTTP sunucusuyla birim testleri. Çalıştırma: python3 -m unittest discover -s tests"""

import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from veriapi import VeriApi, VeriApiHatasi, imza_dogrula  # noqa: E402

KOTA = {"X-RateLimit-Limit-Day": "1000", "X-RateLimit-Remaining-Day": "999", "X-RateLimit-Limit-Minute": "20",
        "X-RateLimit-Remaining-Minute": "19", "X-Istek-Maliyeti": "1"}
ZARF = {"veri": {"kurlar": [{"kod": "USD"}]},
        "kaynak": [{"kod": "ecb", "ad": "ECB", "lisans": "x", "atif": "y", "url": "z"}],
        "guncelleme": "2026-11-05T10:00:00", "bayat": True}


class Sunucu(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.istekler.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        durum, basliklar, govde, gecikme = self.server.yanitlar.pop(0) if self.server.yanitlar else (200, KOTA, ZARF, 0)
        if gecikme:
            time.sleep(gecikme)
        ham = govde if isinstance(govde, bytes) else json.dumps(govde).encode()
        self.send_response(durum)
        for k, v in basliklar.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(ham)))
        self.end_headers()
        try:
            self.wfile.write(ham)
        except BrokenPipeError:  # zaman aşımı testinde istemci çoktan kapatmıştır
            pass

    def log_message(self, *args):
        pass


class Temel(unittest.TestCase):
    def setUp(self):
        self.sunucu = ThreadingHTTPServer(("127.0.0.1", 0), Sunucu)
        self.sunucu.istekler, self.sunucu.yanitlar = [], []
        threading.Thread(target=self.sunucu.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(self.sunucu.server_close)
        self.addCleanup(self.sunucu.shutdown)
        self.beklemeler = []
        self.api = self.istemci()

    def istemci(self, **secenekler):
        api = VeriApi("va_test", temel_adres=f"http://127.0.0.1:{self.sunucu.server_port}/", **secenekler)
        api._uyu = self.beklemeler.append
        return api

    def yanitla(self, *yanitlar):
        self.sunucu.yanitlar.extend((*y, 0) if len(y) == 3 else y for y in yanitlar)

    def son(self):
        yol, basliklar = self.sunucu.istekler[-1]
        parca = urlsplit(yol)
        return parca.path, parse_qs(parca.query), basliklar


class Basari(Temel):
    def test_zarf_ve_kota(self):
        sonuc = self.api.get("/v1/doviz", {"kod": "USD", "bos": None})
        self.assertEqual(sonuc.veri, ZARF["veri"])
        self.assertEqual(sonuc.kaynak[0]["kod"], "ecb")
        self.assertEqual(sonuc.guncelleme, ZARF["guncelleme"])
        self.assertTrue(sonuc.bayat)
        self.assertEqual((sonuc.kota.gunluk_limit, sonuc.kota.gunluk_kalan, sonuc.kota.dakikalik_limit,
                          sonuc.kota.dakikalik_kalan, sonuc.kota.istek_maliyeti), (1000, 999, 20, 19, 1))
        yol, sorgu, basliklar = self.son()
        self.assertEqual((yol, sorgu), ("/v1/doviz", {"kod": ["USD"]}))
        self.assertEqual(basliklar["x-api-key"], "va_test")
        self.assertNotIn("anahtar", self.sunucu.istekler[-1][0])

    def test_kisa_yol_ve_zarfsiz_govde(self):
        self.yanitla((200, {}, {"durum": "ok"}))
        sonuc = self.api.get("durum")
        self.assertEqual(self.son()[0], "/v1/durum")
        self.assertEqual(sonuc.veri, {"durum": "ok"})
        self.assertEqual(sonuc.kaynak, [])
        self.assertIsNone(sonuc.kota.gunluk_kalan)

    def test_kolaylik_yontemleri(self):
        api = self.api
        api.doviz(["USD", "EUR"], tur="referans")
        self.assertEqual(self.son()[:2], ("/v1/doviz", {"kod": ["USD,EUR"], "tur": ["referans"]}))
        api.hava(il="Düzce", ilce="Akçakoca")
        self.assertEqual(self.son()[:2], ("/v1/hava", {"il": ["Düzce"], "ilce": ["Akçakoca"]}))
        api.hava(enlem=0.0, boylam=32.5)
        self.assertEqual(self.son()[:2], ("/v1/hava", {"enlem": ["0.0"], "boylam": ["32.5"]}))
        api.namaz(konum="duzce-akcakoca", tarih="2026-11-05", gun=7)
        self.assertEqual(self.son()[:2], ("/v1/namaz", {"konum": ["duzce-akcakoca"], "tarih": ["2026-11-05"], "gun": ["7"]}))
        api.depremler(limit=5, min_buyukluk=4.5, saat=24, il=81, yurtici=True)
        self.assertEqual(self.son()[:2], ("/v1/deprem/son", {"limit": ["5"], "min_buyukluk": ["4.5"], "saat": ["24"],
                                                             "il": ["81"], "yurtici": ["true"]}))
        api.iller()
        self.assertEqual(self.son()[0], "/v1/konumlar/iller")
        api.ilceler("Düzce/x")
        self.assertIn("/v1/konumlar/iller/D%C3%BCzce%2Fx/ilceler", self.sunucu.istekler[-1][0])


class Hatalar(Temel):
    def test_tipli_hata(self):
        self.yanitla((404, KOTA, {"hata": {"kod": 404, "mesaj": "İl bulunamadı: x"}}))
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.api.ilceler("x")
        hata = baglam.exception
        self.assertEqual((hata.kod, hata.mesaj, hata.http_durumu), (404, "İl bulunamadı: x", 404))
        self.assertEqual(str(hata), "İl bulunamadı: x")
        self.assertEqual(hata.kota.gunluk_kalan, 999)

    def test_422_alanlar(self):
        alanlar = [{"alan": "gun", "mesaj": "çok büyük"}]
        self.yanitla((422, {}, {"hata": {"kod": 422, "mesaj": {"metin": "Geçersiz parametre", "alanlar": alanlar}}}))
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.api.namaz(il="duzce", gun=99)
        self.assertEqual(baglam.exception.mesaj, "Geçersiz parametre")
        self.assertEqual(baglam.exception.alanlar, alanlar)

    def test_json_olmayan_hata(self):
        self.yanitla((502, {"Content-Type": "text/html"}, b"<html>Bad Gateway</html>"))
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.api.iller()
        self.assertEqual((baglam.exception.kod, baglam.exception.http_durumu), (502, 502))

    def test_gecersiz_json_basari(self):
        self.yanitla((200, {}, b"<html>"))
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.api.iller()
        self.assertEqual((baglam.exception.kod, baglam.exception.http_durumu), (0, 200))

    def test_yonlendirme_izlenmez(self):
        self.yanitla((302, {"Location": "/v1/baska"}, b""))
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.api.iller()
        self.assertEqual(baglam.exception.http_durumu, 302)
        self.assertEqual(len(self.sunucu.istekler), 1)

    def test_zaman_asimi(self):
        self.yanitla((200, {}, ZARF, 0.5))
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.istemci(zaman_asimi=0.05).iller()
        self.assertTrue(baglam.exception.zaman_asimi)
        self.assertEqual((baglam.exception.kod, baglam.exception.http_durumu), (0, 0))

    def test_baglanti_hatasi(self):
        api = VeriApi("va_test", temel_adres="http://127.0.0.1:9")
        with self.assertRaises(VeriApiHatasi) as baglam:
            api.iller()
        self.assertFalse(baglam.exception.zaman_asimi)
        self.assertEqual(baglam.exception.http_durumu, 0)

    def test_bos_anahtar(self):
        with self.assertRaises(ValueError):
            VeriApi("")


class YenidenDeneme(Temel):
    SINIR = {"hata": {"kod": 429, "mesaj": "Dakikalık istek sınırı aşıldı"}}

    def test_retry_after_uyulur(self):
        self.yanitla((429, {"Retry-After": "7"}, self.SINIR), (429, {"Retry-After": "3"}, self.SINIR))
        self.assertTrue(self.api.iller().bayat)
        self.assertEqual(self.beklemeler, [7.0, 3.0])
        self.assertEqual(len(self.sunucu.istekler), 3)

    def test_en_cok_iki_deneme(self):
        self.yanitla(*[(429, {"Retry-After": "1"}, self.SINIR)] * 3)
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.api.iller()
        self.assertEqual(baglam.exception.http_durumu, 429)
        self.assertEqual(baglam.exception.retry_after, 1.0)
        self.assertEqual(len(self.sunucu.istekler), 3)

    def test_kapatilabilir(self):
        self.yanitla((429, {"Retry-After": "1"}, self.SINIR))
        with self.assertRaises(VeriApiHatasi):
            self.istemci(yeniden_deneme=0).iller()
        self.assertEqual(self.beklemeler, [])
        self.assertEqual(len(self.sunucu.istekler), 1)

    def test_uzun_bekleme_denenmez(self):
        self.yanitla((429, {"Retry-After": "40000"}, self.SINIR))
        with self.assertRaises(VeriApiHatasi) as baglam:
            self.api.iller()
        self.assertEqual(baglam.exception.retry_after, 40000.0)
        self.assertEqual(self.beklemeler, [])

    def test_baslik_yoksa_varsayilan(self):
        self.yanitla((429, {}, self.SINIR))
        self.api.iller()
        self.assertEqual(self.beklemeler, [1.0])

    def test_baska_hata_denenmez(self):
        self.yanitla((503, {"Retry-After": "1"}, {"hata": {"kod": 503, "mesaj": "hazır değil"}}))
        with self.assertRaises(VeriApiHatasi):
            self.api.iller()
        self.assertEqual(len(self.sunucu.istekler), 1)


class Webhook(unittest.TestCase):
    # Dört SDK'nın testlerinde aynı örnek
    GIZLI = "whsec_ornek"
    ZAMAN = "1762300800"
    GOVDE = '{"olay":"deprem.yeni","veri":{"buyukluk":4.2,"il":"Düzce"}}'
    IMZA = "sha256=c7be673a84807aac83b2d3f90ef21166c532c8006043a2558cefe9c3d839edab"
    SIMDI = 1762300900

    def dogrula(self, govde=GOVDE, imza=IMZA, zaman=ZAMAN, gizli=GIZLI, **secenekler):
        secenekler.setdefault("simdi", self.SIMDI)
        return imza_dogrula(govde, imza, zaman, gizli, **secenekler)

    def test_gecerli(self):
        self.assertTrue(self.dogrula())
        self.assertTrue(self.dogrula(govde=self.GOVDE.encode()))
        self.assertTrue(self.dogrula(imza=self.IMZA.upper().replace("SHA256", "sha256")))

    def test_gecersiz(self):
        self.assertFalse(self.dogrula(govde=self.GOVDE + " "))
        self.assertFalse(self.dogrula(gizli="baska"))
        self.assertFalse(self.dogrula(gizli=""))
        self.assertFalse(self.dogrula(zaman="1762300801"))
        self.assertFalse(self.dogrula(imza=self.IMZA[7:]))
        self.assertFalse(self.dogrula(imza="sha256=zz"))
        self.assertFalse(self.dogrula(imza="sha256=ğğ"))
        self.assertFalse(self.dogrula(imza=None))
        self.assertFalse(self.dogrula(zaman=None))

    def test_tolerans(self):
        self.assertFalse(self.dogrula(simdi=self.SIMDI + 300))
        self.assertTrue(self.dogrula(simdi=self.SIMDI + 300, tolerans=0))
        self.assertTrue(self.dogrula(simdi=self.SIMDI + 300, tolerans=1000))

    def test_iso_zaman(self):
        import hashlib
        import hmac
        zaman = "2026-11-05T00:00:00+03:00"
        ozet = hmac.new(b"g", f"{zaman}.x".encode(), hashlib.sha256).hexdigest()
        an = 1793826000  # 2026-11-04T21:00:00Z
        self.assertTrue(imza_dogrula("x", f"sha256={ozet}", zaman, "g", simdi=an + 10))
        self.assertFalse(imza_dogrula("x", f"sha256={ozet}", zaman, "g", simdi=an + 1000))


if __name__ == "__main__":
    unittest.main()
