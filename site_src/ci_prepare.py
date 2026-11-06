"""GitHub Actions 用：準備每日更新需要、但不放進 GitHub 的大檔案。

目前只有國土測繪中心村界（data.gov.tw 7438，約 21 MB），解壓到 data/raw/village_boundary/。
本機已經有這些檔案就什麼都不做。
"""
import io
import ssl
import zipfile
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "raw" / "village_boundary"
UA = {"User-Agent": "ruisui-life-map/0.1 (GitHub Actions; resident info map)"}


class _GovTLS(HTTPAdapter):
    """部分政府網站憑證缺 Subject Key Identifier；仍驗證憑證，只關掉 X509 嚴格模式。"""

    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)


def main():
    if list(DEST.glob("VILLAGE_NLSC_*.shp")):
        print("村界已存在，略過")
        return
    s = requests.Session()
    s.mount("https://", _GovTLS())
    meta = s.get("https://data.gov.tw/api/v2/rest/dataset/7438", headers=UA, timeout=60).json()["result"]
    url = meta["distribution"][0]["resourceDownloadUrl"]
    r = s.get(url, headers=UA, timeout=600)
    r.raise_for_status()
    DEST.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(DEST)
    print(f"村界下載完成 {len(r.content)/1e6:.1f} MB")


if __name__ == "__main__":
    main()
