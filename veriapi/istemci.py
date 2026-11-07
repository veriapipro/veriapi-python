"""HTTP istemcisi: zarf çözme, tipli hata, 429 yeniden deneme, kolaylık yöntemleri."""

from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

SURUM = "0.1.0"
VARSAYILAN_ADRES = "https://api.veriapi.pro"
VARSAYILAN_BEKLEME = 1.0  # 429'da Retry-After yoksa (saniye)


def _tam(deger: Optional[str]) -> Optional[int]:
    try:
        return int(deger) if deger is not None else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Kota:
    """X-RateLimit-* ve X-Istek-Maliyeti başlıkları; başlık yoksa alan None."""

    gunluk_limit: Optional[int] = None
    gunluk_kalan: Optional[int] = None
    dakikalik_limit: Optional[int] = None
    dakikalik_kalan: Optional[int] = None
    istek_maliyeti: Optional[int] = None

    @classmethod
    def basliklardan(cls, basliklar: Any) -> "Kota":
        return cls(
            gunluk_limit=_tam(basliklar.get("X-RateLimit-Limit-Day")),
            gunluk_kalan=_tam(basliklar.get("X-RateLimit-Remaining-Day")),
            dakikalik_limit=_tam(basliklar.get("X-RateLimit-Limit-Minute")),
            dakikalik_kalan=_tam(basliklar.get("X-RateLimit-Remaining-Minute")),
            istek_maliyeti=_tam(basliklar.get("X-Istek-Maliyeti")),
        )


@dataclass(frozen=True)
class Sonuc:
    """Çözülmüş yanıt zarfı. Zarfsız uçlarda (/v1/durum) `veri` gövdenin tamamıdır."""

    veri: Any
    kaynak: List[Dict[str, Any]] = field(default_factory=list)
    guncelleme: Optional[str] = None
    bayat: bool = False
    kota: Kota = field(default_factory=Kota)


class VeriApiHatasi(Exception):
    """API ya da bağlantı hatası.

    kod: sunucunun `hata.kod` değeri; bağlantı ve zaman aşımında 0.
    http_durumu: HTTP durum kodu; yanıt alınamadıysa 0.
    alanlar: 422'de hatalı parametreler [{"alan", "mesaj"}].
    retry_after: 429'da saniye. kota: yanıttaki kota başlıkları.
    """

    def __init__(self, kod: int, mesaj: str, http_durumu: int, *, alanlar: Optional[List[Dict[str, Any]]] = None,
                 retry_after: Optional[float] = None, kota: Optional[Kota] = None, zaman_asimi: bool = False):
        super().__init__(mesaj)
        self.kod = kod
        self.mesaj = mesaj
        self.http_durumu = http_durumu
        self.alanlar = alanlar or []
        self.retry_after = retry_after
        self.kota = kota or Kota()
        self.zaman_asimi = zaman_asimi


class _YonlendirmeYok(urllib.request.HTTPRedirectHandler):
    """X-API-Key başka bir adrese taşınmasın: yönlendirme izlenmez, 3xx hata olur."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _deger(deger: Any) -> str:
    if isinstance(deger, bool):
        return "true" if deger else "false"
    if isinstance(deger, (list, tuple)):
        return ",".join(str(d) for d in deger)
    return str(deger)


def _retry_after(deger: Optional[str]) -> Optional[float]:
    try:
        return max(0.0, float(deger)) if deger is not None else None
    except ValueError:
        return None


def _hata(durum: int, govde: bytes, basliklar: Any) -> VeriApiHatasi:
    kod, mesaj, alanlar = durum, f"Beklenmeyen yanıt (HTTP {durum})", None
    try:
        hata = json.loads(govde)["hata"]
        kod = int(hata.get("kod", durum))
        ham = hata.get("mesaj")
        if isinstance(ham, dict):  # 422: {"metin": "...", "alanlar": [...]}
            mesaj = str(ham.get("metin") or mesaj)
            alanlar = ham.get("alanlar")
        elif ham:
            mesaj = str(ham)
    except (ValueError, KeyError, TypeError, AttributeError):
        pass  # JSON olmayan yanıt (vekil sunucu 502 sayfası vb.)
    return VeriApiHatasi(kod, mesaj, durum, alanlar=alanlar, kota=Kota.basliklardan(basliklar),
                         retry_after=_retry_after(basliklar.get("Retry-After")))


class VeriApi:
    """VeriApi.Pro istemcisi.

    yeniden_deneme: 429'da en çok kaç kez daha denensin (0 kapatır).
    azami_bekleme: bundan uzun Retry-After beklenmez, hata hemen fırlatılır (günlük kota aşımında saatler sürebilir).
    zaman_asimi: istek başına saniye.
    """

    def __init__(self, anahtar: str, *, temel_adres: str = VARSAYILAN_ADRES, zaman_asimi: float = 10.0,
                 yeniden_deneme: int = 2, azami_bekleme: float = 60.0):
        if not anahtar:
            raise ValueError("API anahtarı gerekli")
        self._anahtar = anahtar
        self._temel = temel_adres.rstrip("/")
        self._zaman_asimi = zaman_asimi
        self._yeniden_deneme = max(0, yeniden_deneme)
        self._azami_bekleme = azami_bekleme
        self._acici = urllib.request.build_opener(_YonlendirmeYok)
        self._uyu = time.sleep

    def get(self, yol: str, parametreler: Optional[Mapping[str, Any]] = None) -> Sonuc:
        """`yol` "/v1/doviz" ya da "doviz" olabilir. None değerli parametreler gönderilmez."""
        url = self._url(yol, parametreler)
        deneme = 0
        while True:
            durum, govde, basliklar = self._istek(url)
            if durum != 429 or deneme >= self._yeniden_deneme:
                break
            bekle = _retry_after(basliklar.get("Retry-After"))
            bekle = VARSAYILAN_BEKLEME if bekle is None else bekle
            if bekle > self._azami_bekleme:
                break
            self._uyu(bekle)
            deneme += 1
        if not 200 <= durum < 300:
            raise _hata(durum, govde, basliklar)
        kota = Kota.basliklardan(basliklar)
        try:
            belge = json.loads(govde)
        except ValueError:
            raise VeriApiHatasi(0, "Yanıt JSON değil", durum, kota=kota) from None
        if not isinstance(belge, dict) or "veri" not in belge:
            return Sonuc(veri=belge, kota=kota)
        return Sonuc(veri=belge["veri"], kaynak=belge.get("kaynak") or [], guncelleme=belge.get("guncelleme"),
                     bayat=bool(belge.get("bayat")), kota=kota)

    def _url(self, yol: str, parametreler: Optional[Mapping[str, Any]]) -> str:
        if not yol.startswith("/v1/"):
            yol = "/v1/" + yol.lstrip("/")
        temiz = {k: _deger(v) for k, v in (parametreler or {}).items() if v is not None}
        if not temiz:
            return self._temel + yol
        ayrac = "&" if "?" in yol else "?"
        return self._temel + yol + ayrac + urllib.parse.urlencode(temiz, quote_via=urllib.parse.quote)

    def _istek(self, url: str) -> Any:
        istek = urllib.request.Request(url, headers={
            "X-API-Key": self._anahtar, "Accept": "application/json", "User-Agent": f"veriapi-python/{SURUM}",
        })
        try:
            with self._acici.open(istek, timeout=self._zaman_asimi) as yanit:
                return yanit.status, yanit.read(), yanit.headers
        except urllib.error.HTTPError as hata:
            with hata:
                return hata.code, hata.read(), hata.headers
        except (TimeoutError, socket.timeout) as hata:
            raise self._zaman_asimi_hatasi() from hata
        except (urllib.error.URLError, http.client.HTTPException, OSError) as hata:
            if isinstance(getattr(hata, "reason", None), (TimeoutError, socket.timeout)):
                raise self._zaman_asimi_hatasi() from hata
            raise VeriApiHatasi(0, f"Bağlantı hatası: {hata}", 0) from hata

    def _zaman_asimi_hatasi(self) -> VeriApiHatasi:
        return VeriApiHatasi(0, f"Zaman aşımı ({self._zaman_asimi} sn)", 0, zaman_asimi=True)

    # --- Kolaylık yöntemleri (parametreler api/app/routers/data.py ile aynı) ---

    def doviz(self, kod: Union[str, Iterable[str], None] = None, tur: Optional[str] = None) -> Sonuc:
        """kod: "USD,EUR" ya da ["USD", "EUR"]; tur: "anlik" | "referans"."""
        if kod is not None and not isinstance(kod, str):
            kod = list(kod)
        return self.get("/v1/doviz", {"kod": kod, "tur": tur})

    def hava(self, *, konum: Optional[str] = None, il: Union[str, int, None] = None, ilce: Optional[str] = None,
             enlem: Optional[float] = None, boylam: Optional[float] = None) -> Sonuc:
        return self.get("/v1/hava", {"konum": konum, "il": il, "ilce": ilce, "enlem": enlem, "boylam": boylam})

    def namaz(self, *, konum: Optional[str] = None, il: Union[str, int, None] = None, ilce: Optional[str] = None,
              enlem: Optional[float] = None, boylam: Optional[float] = None, tarih: Optional[str] = None,
              gun: Optional[int] = None) -> Sonuc:
        """tarih: YYYY-AA-GG (varsayılan bugün); gun: 1-31."""
        return self.get("/v1/namaz", {"konum": konum, "il": il, "ilce": ilce, "enlem": enlem, "boylam": boylam,
                                      "tarih": tarih, "gun": gun})

    def depremler(self, *, limit: Optional[int] = None, min_buyukluk: Optional[float] = None, saat: Optional[int] = None,
                  il: Union[str, int, None] = None, yurtici: Optional[bool] = None) -> Sonuc:
        return self.get("/v1/deprem/son", {"limit": limit, "min_buyukluk": min_buyukluk, "saat": saat, "il": il,
                                           "yurtici": yurtici})

    def iller(self) -> Sonuc:
        return self.get("/v1/konumlar/iller")

    def ilceler(self, il: Union[str, int]) -> Sonuc:
        """il: ad, slug ya da plaka."""
        return self.get(f"/v1/konumlar/iller/{urllib.parse.quote(str(il), safe='')}/ilceler")
