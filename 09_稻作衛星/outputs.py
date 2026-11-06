# -*- coding: utf-8 -*-
"""由 update.py 呼叫：從 NDVI cache 產生田區候選、各村時間序列、疊圖 PNG、縮時 GIF、data/data.js"""
import os, json, datetime as dt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# NDVI 色階（值為 NDVI）
STOPS = [(-0.1, (161, 136, 127)), (0.1, (190, 160, 120)), (0.25, (236, 205, 120)),
         (0.4, (200, 220, 110)), (0.6, (110, 190, 74)), (0.8, (27, 127, 59)), (0.95, (8, 70, 34))]
AMP_MIN = 35      # 田區候選：過去12個月 NDVI(×100) p90-p10 振幅 >= 35
PEAK_MIN = 55     # 且 p90 >= 55（旺季夠綠）
MIN_OBS = 8       # 至少 8 次有效觀測
VILL_MIN_FRAC = 0.25  # 某村該日期有效的田區候選像元比例 < 25% 就不畫點


def colorize(ndvi100):
    """ndvi100: int8 (-128 = 無資料) → RGBA uint8"""
    v = ndvi100.astype("float32") / 100
    xs = [s[0] for s in STOPS]
    rgb = np.stack([np.interp(v, xs, [s[1][c] for s in STOPS]) for c in range(3)], -1)
    a = np.where(ndvi100 == -128, 0, 255).astype("uint8")
    return np.dstack([rgb.astype("uint8"), a])


def to_wgs84(arr, src_transform, dst_shape, dst_transform, nodata, dtype):
    from rasterio.warp import reproject, Resampling
    out = np.full(dst_shape, nodata, dtype)
    reproject(arr, out, src_transform=src_transform, src_crs="EPSG:32651", dst_transform=dst_transform,
              dst_crs="EPSG:4326", resampling=Resampling.nearest, src_nodata=nodata, dst_nodata=nodata)
    return out


def font(sz):
    for f in (r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\msjhbd.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, sz)
    return ImageFont.load_default()


def build(vill, bounds, shape, transform, lab, ordered, CACHE, OVER, DATA, HERE):
    from rasterio.transform import from_bounds
    import geopandas as gpd
    usable = [s for s in ordered if s["usable"]]
    if not usable:
        print("沒有可用影像"); return
    last_date = dt.date.fromisoformat(usable[-1]["date"])
    mask_town = lab > 0
    names = list(vill.VILLNAME)

    load = lambda d: np.load(os.path.join(CACHE, d + ".npy"))
    # ---- 田區候選（過去 12 個月）----
    y1 = [s for s in usable if (last_date - dt.date.fromisoformat(s["date"])).days <= 365]
    H, W = shape
    p10 = np.zeros(shape, "float32"); p90 = np.zeros(shape, "float32"); nobs = np.zeros(shape, "int16")
    stack = np.stack([load(s["date"]) for s in y1])  # n,H,W int8
    for r0 in range(0, H, 200):
        blk = stack[:, r0:r0 + 200].astype("float32")
        blk[blk == -128] = np.nan
        nobs[r0:r0 + 200] = np.sum(~np.isnan(blk), 0)
        with np.errstate(all="ignore"):
            q = np.nanpercentile(blk, [10, 90], axis=0)
        p10[r0:r0 + 200], p90[r0:r0 + 200] = q[0], q[1]
    farm = mask_town & (nobs >= MIN_OBS) & ((p90 - p10) >= AMP_MIN) & (p90 >= PEAK_MIN)
    del stack
    print("田區候選像元", int(farm.sum()), "占鄉域", round(farm.sum() / mask_town.sum() * 100, 1), "%")

    # ---- 各村時間序列（24 個月內可用影像）----
    series = {n: [] for n in names}
    farm_by_v = [(lab == i + 1) & farm for i in range(len(names))]
    all_by_v = [lab == i + 1 for i in range(len(names))]
    dates = [s["date"] for s in usable]
    for s in usable:
        nd = load(s["date"])
        ok = nd != -128
        for i, n in enumerate(names):
            f = farm_by_v[i]
            nf = f.sum()
            vf = f & ok
            med = float(np.median(nd[vf])) / 100 if nf and vf.sum() / nf >= VILL_MIN_FRAC else None
            va = all_by_v[i] & ok
            meda = float(np.median(nd[va])) / 100 if va.sum() / all_by_v[i].sum() >= VILL_MIN_FRAC else None
            series[n].append({"d": s["date"], "farm": None if med is None else round(med, 3),
                              "all": None if meda is None else round(meda, 3)})

    # ---- 疊圖與 GIF（WGS84）----
    x0, y0, x1, y1_ = bounds
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32651", "EPSG:4326", always_xy=True)
    xs, ys = tr.transform([x0, x1, x0, x1], [y0, y0, y1_, y1_])
    w_, e_, s_, n_ = min(xs), max(xs), min(ys), max(ys)
    dw = int(round((e_ - w_) / 0.0001)); dh = int(round((n_ - s_) / 0.0001))
    dtf = from_bounds(w_, s_, e_, n_, dw, dh)
    town_w = to_wgs84(mask_town.astype("uint8"), transform, (dh, dw), dtf, 0, "uint8").astype(bool)
    farm_w = to_wgs84(farm.astype("uint8"), transform, (dh, dw), dtf, 0, "uint8").astype(bool)

    # 村界輪廓（WGS84 像素）
    vg = vill.to_crs("EPSG:4326")
    def px(lon, lat):
        return ((lon - w_) / (e_ - w_) * dw, (n_ - lat) / (n_ - s_) * dh)
    outline_geo = json.loads(vg.to_json())

    for f in os.listdir(OVER):
        if f.endswith(".png"):
            os.remove(os.path.join(OVER, f))   # 只清自己產生的疊圖(本程式專屬資料夾)
    ovl_scenes = [s for s in usable if (last_date - dt.date.fromisoformat(s["date"])).days <= 365]
    frames = []
    GW = 560; GH = int(GW * dh / dw)
    for s in ovl_scenes:
        nd = to_wgs84(load(s["date"]), transform, (dh, dw), dtf, -128, "int8")
        nd[~town_w] = -128
        im = Image.fromarray(colorize(nd), "RGBA")
        im.save(os.path.join(OVER, s["date"] + ".png"), optimize=True)
        s["overlay"] = f"overlays/{s['date']}.png"
        # GIF 影格：背景淡灰 + 鄉域 NDVI + 村界 + 日期
        bg = Image.new("RGB", (dw, dh), (244, 242, 236))
        bg.paste(im.convert("RGB"), mask=im.split()[3])
        d = ImageDraw.Draw(bg)
        for ft in outline_geo["features"]:
            geom = ft["geometry"]
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            for poly in polys:
                for ring in poly:
                    d.line([px(*pt) for pt in ring], fill=(255, 255, 255), width=3)
        bg = bg.resize((GW, GH), Image.LANCZOS)
        d = ImageDraw.Draw(bg)
        d.rectangle([0, 0, GW, 34], fill=(255, 255, 255))
        d.text((10, 4), f"{s['date']}  NDVI（雲遮罩後可用 {int(s['valid_frac']*100)}%）", fill=(30, 40, 30), font=font(20))
        frames.append(bg)
    if frames:
        pal = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames]
        pal[0].save(os.path.join(HERE, "timelapse.gif"), save_all=True, append_images=pal[1:],
                    duration=700, loop=0, optimize=True)
        frames[-1].save(os.path.join(HERE, "preview_last_frame.png"))

    # 田區候選圖（供頁面說明）
    fm = np.where(farm_w, 1, 0).astype("uint8")
    rgba = np.zeros((dh, dw, 4), "uint8"); rgba[farm_w] = (255, 140, 0, 200)
    Image.fromarray(rgba, "RGBA").save(os.path.join(OVER, "_farm_mask.png"), optimize=True)

    # ---- 各村統計卡 ----
    cards = []
    for i, n in enumerate(names):
        ser = series[n]
        pts = [p for p in ser if p["farm"] is not None]
        cur = pts[-1] if pts else None
        prev = None
        if cur:
            cd = dt.date.fromisoformat(cur["d"])
            cand = [p for p in pts if 20 <= (cd - dt.date.fromisoformat(p["d"])).days <= 50]
            prev = cand[-1] if cand else None
        cards.append({"name": n, "farm_px": int(farm_by_v[i].sum()),
                      "farm_ha": round(float(farm_by_v[i].sum()) * 100 / 10000, 1),
                      "latest": cur, "prev": prev})

    all_scenes = [{"date": s["date"], "valid_frac": s["valid_frac"], "stac_cloud_pct": s["stac_cloud_pct"],
                   "tiles": s["tiles"], "usable": s["usable"], "overlay": s.get("overlay")} for s in ordered]
    # 近 12 個月可用影像的鄉域田區候選中位數（供滑桿顯示）
    payload = {"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "bounds": [[s_, w_], [n_, e_]], "villages": outline_geo,
               "scenes": all_scenes, "series": series, "cards": cards,
               "params": {"amp_min": AMP_MIN, "peak_min": PEAK_MIN, "min_obs": MIN_OBS,
                          "vill_min_frac": VILL_MIN_FRAC, "min_valid": 0.5,
                          "farm_px": int(farm.sum()), "town_px": int(mask_town.sum()),
                          "n_year_obs": len(y1)},
               "latest": usable[-1]["date"]}
    with open(os.path.join(DATA, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.DATA=" + json.dumps(payload, ensure_ascii=False) + ";")
    print("輸出完成：可用", len(usable), "/", len(ordered), "；最近可用", usable[-1]["date"])
