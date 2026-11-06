# -*- coding: utf-8 -*-
"""瑞穗生活地圖 #9 稻作與文旦衛星 — 更新程式

每次執行：
 1. 向 Element84 Earth Search (AWS STAC) 查 Sentinel-2 L2A 影像（瑞穗範圍）
 2. 只處理 cache 裡還沒有的日期（有新影像才處理），只讀瑞穗範圍的裁切（COG 範圍讀取）
 3. 用 SCL 雲遮罩算每張的可用比例；低於門檻者標記「略過」，仍記錄日期與雲量
 4. 算 NDVI、各村時間序列、最近一張可用影像的疊圖、縮時 GIF
 5. 寫出 data/scenes.json（每張影像日期、雲量、可用比例、狀態）與 data/data.js（給 index.html）

用法： python -I update.py [--months 24] [--min-valid 0.5] [--rebuild]
"""
import os, sys, json, ssl, argparse, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import requests
from requests.adapters import HTTPAdapter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "ndvi_cache_v2")
OVER = os.path.join(HERE, "overlays")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("GDAL_HTTP_VERSION", "2")
for d in (DATA, CACHE, OVER):
    os.makedirs(d, exist_ok=True)

VILLAGE_SHP = r"C:\Archive\04_IG貼文創作\瑞穗生活地圖\data\raw\village_boundary\VILLAGE_NLSC_1150817.shp"
LOCAL_VILL = os.path.join(DATA, "villages.geojson")
STAC = "https://earth-search.aws.element84.com/v1/search"
CRS_UTM = "EPSG:32651"
RES = 10
# SCL：4 植被 5 裸地 6 水；其餘（雲、雲影、飽和、無資料、未分類、雪）視為不可用
VALID_SCL = (4, 5, 6)


class _Adapter(HTTPAdapter):  # Python 3.14 對部分憑證嚴格檢查的解法（本例 AWS 通常不需要，保險用）
    def init_poolmanager(self, *a, **k):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        k["ssl_context"] = ctx
        super().init_poolmanager(*a, **k)


def session():
    s = requests.Session()
    s.mount("https://", _Adapter())
    return s


def load_villages():
    import geopandas as gpd
    if os.path.exists(VILLAGE_SHP):
        g = gpd.read_file(VILLAGE_SHP, encoding="utf-8")
        g = g[(g.COUNTYNAME == "花蓮縣") & (g.TOWNNAME == "瑞穗鄉")][["VILLCODE", "VILLNAME", "geometry"]]
        g.to_file(LOCAL_VILL, driver="GeoJSON")
    else:
        g = gpd.read_file(LOCAL_VILL)
    assert len(g) == 11, len(g)
    return g.to_crs(CRS_UTM).reset_index(drop=True)


def grid(vill):
    x0, y0, x1, y1 = vill.total_bounds
    x0 = np.floor(x0 / RES) * RES; y0 = np.floor(y0 / RES) * RES
    x1 = np.ceil(x1 / RES) * RES; y1 = np.ceil(y1 / RES) * RES
    W = int((x1 - x0) / RES); H = int((y1 - y0) / RES)
    from rasterio.transform import from_origin
    return (x0, y0, x1, y1), (H, W), from_origin(x0, y1, RES, RES)


def village_labels(vill, shape, transform):
    from rasterio.features import rasterize
    lab = rasterize([(g, i + 1) for i, g in enumerate(vill.geometry)], out_shape=shape,
                    transform=transform, fill=0, dtype="uint8")
    return lab


def search(months):
    s = session()
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=int(months * 30.5))
    items, page = [], 1
    body = {"collections": ["sentinel-2-l2a"], "bbox": [121.32, 23.43, 121.50, 23.60],
            "datetime": f"{start}T00:00:00Z/{end}T00:00:00Z", "limit": 100}
    url, method = STAC, "post"
    while True:
        r = s.post(url, json=body, timeout=90)
        r.raise_for_status()
        j = r.json()
        items += j["features"]
        nxt = [l for l in j.get("links", []) if l["rel"] == "next"]
        if not nxt:
            break
        body = nxt[0].get("body", body)
        url = nxt[0]["href"]
    return items


def read_win(href, bounds, shape, resamp):
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.enums import Resampling
    import time
    for attempt in range(5):  # 網路偶爾解析失敗，重試
        try:
            with rasterio.open(href) as ds:
                win = from_bounds(*bounds, transform=ds.transform)
                return ds.read(1, window=win, out_shape=shape, boundless=True, fill_value=0,
                               resampling=Resampling.nearest if resamp == "n" else Resampling.bilinear)
        except rasterio.errors.RasterioIOError:
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))


def process_date(date, items, bounds, shape, mask_town):
    """同一天可能有兩個圖幅(51QUF/51QUG)，合併後算 NDVI。回傳 (record, ndvi_int8)"""
    H, W = shape
    ndvi = np.full(shape, -128, np.int8)
    ok = np.zeros(shape, bool)
    clouds = []
    for it in items:
        a = it["assets"]
        clouds.append(it["properties"].get("eo:cloud_cover"))
        scl = read_win(a["scl"]["href"], bounds, shape, "n")
        red = read_win(a["red"]["href"], bounds, shape, "b").astype("float32")
        nir = read_win(a["nir"]["href"], bounds, shape, "b").astype("float32")
        # 注意：Earth Search 中繼資料標示 offset=-0.1，但實測（2026-11-05，水體 NIR DN 中位數≈225、
        # 森林紅光≈216）DN 並未含 +1000 偏移，套用會得到 NDVI>1。故不扣偏移（offset=0）。
        # 若日後 Earth Search 改版，請用水體像元 NIR 的 DN 重新確認。
        off = 0.0
        sc = a["red"]["raster:bands"][0].get("scale", 0.0001)
        r = red * sc + off; n = nir * sc + off
        v = np.isin(scl, VALID_SCL) & (red > 0) & (nir > 0) & ~ok
        with np.errstate(divide="ignore", invalid="ignore"):
            nd = (n - r) / (n + r)
        v &= np.isfinite(nd)
        ndvi[v] = np.clip(np.round(nd[v] * 100), -100, 100).astype(np.int8)
        ok |= v
    valid_frac = float((ok & mask_town).sum() / mask_town.sum())
    rec = {"date": str(date), "ids": [i["id"] for i in items],
           "tiles": [i["properties"].get("grid:code") for i in items],
           "stac_cloud_pct": [None if c is None else round(c, 1) for c in clouds],
           "valid_frac": round(valid_frac, 3)}
    return rec, ndvi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=float, default=24)
    ap.add_argument("--min-valid", type=float, default=0.5)
    ap.add_argument("--rebuild", action="store_true", help="只用 cache 重建輸出，不連網")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # -I 會忽略 PYTHONIOENCODING
    except Exception:
        pass

    vill = load_villages()
    bounds, shape, transform = grid(vill)
    lab = village_labels(vill, shape, transform)
    mask_town = lab > 0

    scenes_path = os.path.join(DATA, "scenes.json")
    scenes = {}
    if os.path.exists(scenes_path):
        scenes = {s["date"]: s for s in json.load(open(scenes_path, encoding="utf-8"))["scenes"]}

    for f in os.listdir(CACHE):  # 取回上次中斷前已存的單日紀錄
        if f.endswith(".json") and f[:-5] not in scenes and os.path.exists(os.path.join(CACHE, f[:-5] + ".npy")):
            scenes[f[:-5]] = json.load(open(os.path.join(CACHE, f), encoding="utf-8"))
    new = 0
    if not args.rebuild:
        items = search(args.months)
        by = {}
        for it in items:
            d = it["properties"]["datetime"][:10]
            by.setdefault(d, []).append(it)
        todo = sorted(d for d in by if d not in scenes or not os.path.exists(os.path.join(CACHE, d + ".npy")))
        print(f"STAC 找到 {len(by)} 個日期；新的 {len(todo)} 個", flush=True)

        def job(d):
            try:
                rec, nd = process_date(d, by[d], bounds, shape, mask_town)
                np.save(os.path.join(CACHE, d + ".npy"), nd)
                json.dump(rec, open(os.path.join(CACHE, d + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
                return d, rec, None
            except Exception as e:  # noqa
                return d, None, repr(e)

        with ThreadPoolExecutor(args.workers) as ex:
            for d, rec, err in ex.map(job, todo):
                if err:
                    print("失敗", d, err, flush=True)
                    continue
                scenes[d] = rec
                new += 1
                print(d, "可用比例", rec["valid_frac"], "STAC雲量", rec["stac_cloud_pct"], flush=True)
        if new == 0:
            print("沒有新影像。")
    for s in scenes.values():
        s["usable"] = s["valid_frac"] >= args.min_valid
        s["status"] = "使用" if s["usable"] else "略過（雲遮後可用比例過低）"
    ordered = sorted(scenes.values(), key=lambda s: s["date"])
    json.dump({"updated": str(dt.datetime.now())[:19], "min_valid": args.min_valid,
               "source": "Element84 Earth Search (AWS STAC) sentinel-2-l2a", "scenes": ordered},
              open(scenes_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if new == 0 and not args.rebuild and os.path.exists(os.path.join(DATA, "data.js")):
        print("沒有新影像且輸出已存在，略過重建（要強制重建請加 --rebuild）。")
        return
    build_outputs(vill, bounds, shape, transform, lab, ordered)


def build_outputs(vill, bounds, shape, transform, lab, ordered):
    from outputs import build
    build(vill, bounds, shape, transform, lab, ordered, CACHE, OVER, DATA, HERE)


if __name__ == "__main__":
    sys.path.insert(0, HERE)  # -I 模式下不會自動加入
    main()
