"""在本機執行：產生雲端（GitHub Actions）抓不到時用的備份檔，放在 data/fallback/。

GitHub 的機器在國外，以下兩個政府網站會擋（2026-11-06 第一次執行確認）：
- 國土測繪中心村界（tgos.tw）→ 403
- 衛福部 AED（tw-aed.mohw.gov.tw）→ 連線逾時
所以把「花蓮縣」範圍的子集存進 repo。村界與 AED 位置很少變動，建議每 1 到 3 個月在本機重跑一次後 commit。

用法：python -I site_src/make_fallbacks.py
"""
import csv
import shutil
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
FB = ROOT / "data" / "fallback"


def villages():
    shp = next((RAW / "village_boundary").glob("VILLAGE_NLSC_*.shp"))
    g = gpd.read_file(shp)
    col = next(c for c in g.columns if c.upper() in ("COUNTYNAME", "COUNTY"))
    h = g[g[col] == "花蓮縣"]
    out = FB / "village_boundary"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    h.to_file(out / shp.name, encoding="utf-8")
    print(f"村界：{shp.name} 花蓮縣 {len(h)} 村里")


def aed():
    src = RAW / "aed.csv"
    with open(src, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        rows = [x for x in r if x["場所縣市"] == "花蓮縣"]
        fields = r.fieldnames
    with open(FB / "aed.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"AED：花蓮縣 {len(rows)} 台")


if __name__ == "__main__":
    FB.mkdir(parents=True, exist_ok=True)
    villages()
    aed()
