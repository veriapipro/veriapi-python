"""Webhook imza doğrulama: imzalanan metin `<zaman>.<ham gövde>`, HMAC-SHA256 hex, başlık `sha256=<hex>`."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import datetime
from typing import Optional, Union

_IMZA = re.compile(r"sha256=([0-9a-fA-F]{64})")
_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\S+(Z|[+-]\d{2}:\d{2})")
_SAYI = re.compile(r"\d+(\.\d+)?")


def _saniye(zaman: str) -> Optional[float]:
    """Unix saniyesi ya da saat dilimli ISO 8601; başka biçim None."""
    if _SAYI.fullmatch(zaman):
        return float(zaman)
    if _RFC3339.fullmatch(zaman):
        try:
            return datetime.fromisoformat(zaman.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def imza_dogrula(govde: Union[bytes, str], imza: Optional[str], zaman: Optional[str], gizli: Union[bytes, str],
                 tolerans: float = 300, simdi: Optional[float] = None) -> bool:
    """X-VeriApi-Imza başlığını doğrular; geçersiz her girdide False döner, hata fırlatmaz.

    govde: ayrıştırılmamış ham gövde (JSON'u yeniden üretmeyin, imza tutmaz).
    imza, zaman: X-VeriApi-Imza ve X-VeriApi-Zaman başlıkları.
    tolerans: yeniden oynatmaya karşı saniye cinsinden zaman farkı sınırı (0 kapatır).
    """
    if not gizli or not imza or not zaman:
        return False
    eslesme = _IMZA.fullmatch(imza.strip())
    if not eslesme:
        return False
    zaman = zaman.strip()
    ham = govde.encode("utf-8") if isinstance(govde, str) else bytes(govde)
    anahtar = gizli.encode("utf-8") if isinstance(gizli, str) else gizli
    beklenen = hmac.new(anahtar, zaman.encode("utf-8") + b"." + ham, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(beklenen.encode("ascii"), eslesme.group(1).lower().encode("ascii")):
        return False
    if tolerans <= 0:
        return True
    saniye = _saniye(zaman)
    if saniye is None:
        return False
    return abs((time.time() if simdi is None else simdi) - saniye) <= tolerans
