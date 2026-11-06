"""GitHub Actions 用：準備每日更新需要、但不放進 GitHub 的大檔案。

- 村界：先試國土測繪中心（data.gov.tw 7438），境外被擋時改用 data/fallback/village_boundary/。
- AED：先放 data/fallback/aed.csv，fetch_daily.py 下載成功會覆蓋。
本機已經有這些檔案就什麼都不做。
"""
import io
import os
import shutil
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


FB = ROOT / "data" / "fallback"  # make_fallbacks.py 在本機產生的花蓮縣子集


def download_villages():
    s = requests.Session()
    s.mount("https://", _GovTLS())
    meta = s.get("https://data.gov.tw/api/v2/rest/dataset/7438", headers=UA, timeout=60).json()["result"]
    url = meta["distribution"][0]["resourceDownloadUrl"]
    r = s.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    DEST.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(DEST)
    print(f"村界下載完成 {len(r.content)/1e6:.1f} MB")


def main():
    force = os.environ.get("CI_FORCE_FALLBACK") == "1"  # 測試用：強制使用備份
    if not list(DEST.glob("VILLAGE_NLSC_*.shp")):
        try:
            if force:
                raise RuntimeError("CI_FORCE_FALLBACK=1")
            download_villages()
        except Exception as e:  # noqa: BLE001（境外 IP 會被 tgos.tw 擋 403）
            print(f"村界下載失敗（{str(e)[:80]}），改用 data/fallback 的花蓮縣子集")
            shutil.copytree(FB / "village_boundary", DEST, dirs_exist_ok=True)
    # AED：先放備份，fetch_daily.py 下載成功就會覆蓋
    aed = ROOT / "data" / "raw" / "aed.csv"
    if not aed.exists() or force:
        aed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FB / "aed.csv", aed)
        print("AED 先放入備份檔（花蓮縣子集）")


if __name__ == "__main__":
    main()
