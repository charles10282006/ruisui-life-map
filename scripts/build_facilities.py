"""整理設施清單 → web/data/facilities.json

設施類別：
  clinic   西醫診所／衛生所     dental 牙醫     tcm 中醫
  pharmacy 健保藥局             hospital 醫院（er=True 表示有急診）
  aed      AED                  station 台鐵車站      bus 客運站牌

定位（precision）：
  exact  來源本身有座標（AED、客運站牌、OSM 標點、手動校正）
  road   只定位到路段，誤差可能數百公尺，網頁上標「約略位置」

手動校正：data/geocode_overrides.csv（欄位 地址,lat,lon,備註），優先於一切。
"""
import csv
import json
import re
import time
import unicodedata
import requests
from shapely.geometry import Point
import geopandas as gpd

from common import RAW, DATA, WEB_DATA, UA, TODAY, NEAR_TOWNS, FAC_BBOX as NET_BBOX, OVERPASS_URLS

CACHE = DATA / "geocode_cache.csv"
OVERRIDES = DATA / "geocode_overrides.csv"
OSM_POI = DATA / "osm_poi.json"


def norm(s):
    """全形轉半形、去空白。"""
    return unicodedata.normalize("NFKC", s or "").replace(" ", "").strip()


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------- 定位 ----------
def load_table(path):
    if not path.exists():
        return {}
    return {norm(r["地址"]): r for r in read_csv(path)}


def fetch_osm_poi():
    """OSM 的醫療設施與車站標點（快取一份，每次重建時更新）。"""
    s, w, n, e = NET_BBOX
    q = (f'[out:json][timeout:90];('
         f'nwr["amenity"~"clinic|doctors|pharmacy|hospital|dentist"]({s},{w},{n},{e});'
         f'nwr["railway"="station"]({s},{w},{n},{e}););out center tags;')
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": q}, headers={"User-Agent": UA}, timeout=150)
            if r.ok and r.text.lstrip().startswith("{"):
                OSM_POI.write_text(r.text, encoding="utf-8")
                break
        except Exception:
            continue
    if not OSM_POI.exists():
        return []
    out = []
    for el in json.loads(OSM_POI.read_text(encoding="utf-8"))["elements"]:
        t = el.get("tags", {})
        c = el.get("center", el)
        if "lat" in c and t.get("name"):
            out.append({"name": norm(t["name"]), "lat": c["lat"], "lon": c["lon"],
                        "kind": t.get("amenity") or t.get("railway")})
    return out


OSM_ALIAS = {  # 健保名稱 → OSM 名稱
    "臺灣基督教門諾會醫療財團法人門諾醫院": "基督教門諾會醫院",
}


GENERIC = re.compile(r"^(藥局|西藥房|藥房|診所|牙醫|牙醫診所|中醫診所|醫院|衛生所|衛生室)$")
MAX_DEG = 0.08  # 約 8 公里：OSM 標點必須在該鄉鎮中心附近，避免同名店錯配到別的鄉鎮


def osm_match(name, osm, town_pt=None):
    """名稱比對（不含車站、不含「藥局」這類通用名）：
    先找完全相同；再去掉「醫療財團法人」等前綴後互相包含，取名稱最長者（最具體）。"""
    n = OSM_ALIAS.get(norm(name), norm(name))
    core = re.sub(r"^(佛教慈濟醫療財團法人|衛生福利部|臺北榮民總醫院|花蓮縣)", "", n)

    def ok(p):
        if p["kind"] == "station" or GENERIC.match(p["name"]) or len(p["name"]) < 4:
            return False
        if town_pt and ((p["lat"] - town_pt[0]) ** 2 + (p["lon"] - town_pt[1]) ** 2) ** 0.5 > MAX_DEG:
            return False
        return True

    pool = [p for p in osm if ok(p)]
    exact = [p for p in pool if p["name"] == n]
    if exact:
        return exact[0]
    cands = [p for p in pool if len(core) >= 4 and (core in p["name"] or p["name"] in n)]
    if not cands:
        return None
    cands.sort(key=lambda p: -len(p["name"]) if p["name"] in n else len(p["name"]))
    return cands[0]


ROAD_RE = re.compile(r"(?:[鄉鎮市])(?:\D{1,3}[村里])?(?:\d+鄰)?(.+?(?:路|街|大道)(?:[一二三四五六七八九十\d]+段)?)")


def road_geocode(addr, town, cache):
    """用 Nominatim 只定位到路段（瑞穗等鄉鎮的門牌在 OSM 幾乎沒有）。"""
    key = norm(addr)
    if key in cache and cache[key].get("lat"):
        c = cache[key]
        return float(c["lat"]), float(c["lon"])
    m = ROAD_RE.search(norm(addr).replace("花蓮縣", ""))
    res = None
    if m:
        road = m.group(1)
        road = re.sub(r"(\d+)段", lambda x: "一二三四五六七八九"[int(x.group(1)) - 1] + "段", road)
        q = f"{road}, {town}, 花蓮縣"
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"format": "json", "limit": 1, "countrycodes": "tw", "q": q},
                             headers={"User-Agent": UA}, timeout=30)
            time.sleep(1.1)  # Nominatim 使用規範：每秒最多 1 次
            js = r.json() if r.ok else []
            if js:
                res = (float(js[0]["lat"]), float(js[0]["lon"]))
        except Exception:
            pass
    if res:  # 失敗不寫快取，下次重查
        cache[key] = {"地址": key, "lat": res[0], "lon": res[1], "來源": "nominatim-road"}
    return res


def save_cache(cache):
    with open(CACHE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["地址", "lat", "lon", "來源"])
        w.writeheader()
        for r in cache.values():
            w.writerow({k: r.get(k, "") for k in w.fieldnames})


# ---------- 院所 ----------
def active(row):
    d = norm(row.get("終止合約或歇業日期", ""))
    return not d or d > TODAY.strftime("%Y%m%d")


def town_of(addr):
    a = norm(addr)
    if not a.startswith("花蓮縣"):
        return None
    for t in ["瑞穗鄉", "光復鄉", "玉里鎮", "鳳林鎮", "萬榮鄉", "卓溪鄉", "富里鄉", "花蓮市", "吉安鄉", "壽豐鄉", "新城鄉", "豐濱鄉", "秀林鄉"]:
        if a.startswith("花蓮縣" + t):
            return t
    return None


def category(row, hospital=False):
    if hospital:
        return "hospital"
    kind = row["醫事機構種類"]
    if "牙醫" in kind:
        return "dental"
    if "中醫" in kind:
        return "tcm"
    return "clinic"


def build():
    hours = {r["醫事機構代碼"]: r for r in read_csv(RAW / "nhi_hours.csv")}
    cache = load_table(CACHE)
    overrides = load_table(OVERRIDES)
    osm = fetch_osm_poi()
    villages = gpd.read_file(next((RAW / "village_boundary").glob("VILLAGE_NLSC_*.shp")))
    towns = villages[villages.COUNTYNAME == "花蓮縣"].dissolve("TOWNNAME")
    town_center = {t: (g.centroid.y, g.centroid.x) for t, g in towns.geometry.items()}

    facs, missing = [], []

    def add(rec, addr, town):
        key = norm(addr)
        if key in overrides and overrides[key].get("lat"):
            rec.update(lat=float(overrides[key]["lat"]), lon=float(overrides[key]["lon"]), precision="exact", geo="手動校正")
        else:
            m = osm_match(rec["name"], osm, town_center.get(town))
            if m and m["kind"] not in ("station",):
                rec.update(lat=m["lat"], lon=m["lon"], precision="exact", geo="OpenStreetMap")
            else:
                ll = road_geocode(addr, town, cache)
                if ll:
                    rec.update(lat=ll[0], lon=ll[1], precision="road", geo="路段")
                else:
                    # 沒有路名（只有村、鄰、號）→ 定位到村里範圍內的代表點
                    vm = re.search(town + r"(\D{1,3}?[村里])", norm(addr))
                    vrow = villages[(villages.TOWNNAME == town) & (villages.VILLNAME == vm.group(1))] if vm else None
                    if vrow is None or vrow.empty:
                        missing.append(f'{rec["name"]}（{addr}）')
                        return
                    p = vrow.geometry.iloc[0].representative_point()
                    rec.update(lat=p.y, lon=p.x, precision="village", geo="村里代表點")
        facs.append(rec)

    # 西醫／牙醫／中醫診所與衛生所：只取鄰近鄉鎮
    for row in read_csv(RAW / "nhi_clinic.csv"):
        town = town_of(row["地址"])
        if town not in NEAR_TOWNS or not active(row):
            continue
        h = hours.get(row["醫事機構代碼"], {})
        add({"id": row["醫事機構代碼"], "cat": category(row), "name": norm(row["醫事機構名稱"]),
             "addr": norm(row["地址"]), "tel": norm(row["電話"]), "town": town,
             "dept": norm(row["診療科別"]), "hours": h.get("看診星期", ""), "note": norm(h.get("看診備註", "")).strip("-")},
            row["地址"], town)

    # 藥局
    for row in read_csv(RAW / "nhi_pharmacy.csv"):
        town = town_of(row["地址"])
        if town not in NEAR_TOWNS or not active(row):
            continue
        h = hours.get(row["醫事機構代碼"], {})
        add({"id": row["醫事機構代碼"], "cat": "pharmacy", "name": norm(row["醫事機構名稱"]),
             "addr": norm(row["地址"]), "tel": norm(row["電話"]), "town": town,
             "hours": h.get("看診星期", ""), "note": norm(h.get("看診備註", "")).strip("-")},
            row["地址"], town)

    # 醫院：花蓮縣全部（含花蓮市的大醫院）
    # 急診判斷：健保登記「急診業務」，且（有急診醫學科，或列於健保署 111 年
    # 「緊急醫療資源不足地區急救責任醫院」名單）。只有急診業務、沒有急診醫學科的
    # 精神科醫院（如衛生福利部玉里醫院）不列為一般急診。
    ER_ALLOW = {"佛教慈濟醫療財團法人玉里慈濟醫院", "臺北榮民總醫院玉里分院",
                "臺北榮民總醫院鳳林分院", "衛生福利部花蓮醫院豐濱原住民分院"}

    def is_er(row):
        name = norm(row["醫事機構名稱"])
        return "急診業務" in row["服務項目"] and ("急診醫學科" in row["診療科別"] or name in ER_ALLOW)

    seen = set()
    for f in ["nhi_hosp_center.csv", "nhi_hosp_regional.csv", "nhi_hosp_district.csv"]:
        for row in read_csv(RAW / f):
            town = town_of(row["地址"])
            if not town or not active(row) or row["醫事機構代碼"] in seen:
                continue
            seen.add(row["醫事機構代碼"])
            h = hours.get(row["醫事機構代碼"], {})
            add({"id": row["醫事機構代碼"], "cat": "hospital", "name": norm(row["醫事機構名稱"]),
                 "addr": norm(row["地址"]), "tel": norm(row["電話"]), "town": town,
                 "er": is_er(row), "dept": norm(row["診療科別"]),
                 "hours": h.get("看診星期", ""), "note": norm(h.get("看診備註", "")).strip("-")},
                row["地址"], town)

    # AED（衛福部，自帶座標）
    for row in read_csv(RAW / "aed.csv"):
        if row["場所縣市"] != "花蓮縣" or row["場所區域"] not in NEAR_TOWNS:
            continue
        try:
            lat, lon = float(row["地點LAT"]), float(row["地點LNG"])
        except ValueError:
            continue
        facs.append({"id": "AED" + row["AEDID"], "cat": "aed", "name": norm(row["場所名稱"]),
                     "addr": norm(row["場所地址"]), "spot": norm(row["AED放置地點"]), "town": row["場所區域"],
                     "tel": norm(row["開放時間緊急連絡電話"]),
                     "open": [row["周一至周五起"][:5], row["周一至周五迄"][:5], row["周六起"][:5],
                              row["周六迄"][:5], row["周日起"][:5], row["周日迄"][:5]],
                     "open_note": norm(row["開放使用時間備註"]),
                     "lat": lat, "lon": lon, "precision": "exact", "geo": "衛福部AED"})

    # 台鐵車站（OSM）
    s, w, n, e = NET_BBOX
    for p in osm:
        if p["kind"] == "station" and s <= p["lat"] <= n and w <= p["lon"] <= e:
            facs.append({"id": "TRA_" + p["name"], "cat": "station", "name": p["name"] + "車站",
                         "lat": p["lat"], "lon": p["lon"], "precision": "exact", "geo": "OpenStreetMap"})

    # 客運站牌（公路局，自帶座標）
    stops = {}
    for row in read_csv(RAW / "bus" / "timetable.csv"):
        try:
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
        except ValueError:
            continue
        if not (s <= lat <= n and w <= lon <= e):
            continue
        sid = row["站位ID"]
        st = stops.setdefault(sid, {"id": "BUS" + sid, "cat": "bus", "name": norm(row["站名"]),
                                     "lat": lat, "lon": lon, "routes": set(), "precision": "exact", "geo": "公路局"})
        st["routes"].add(row["路線編號"])
    for st in stops.values():
        st["routes"] = sorted(st["routes"])
        facs.append(st)

    save_cache(cache)
    if not OVERRIDES.exists():
        with open(OVERRIDES, "w", encoding="utf-8-sig", newline="") as f:
            f.write("地址,lat,lon,備註\n")

    meta = {"built": TODAY.isoformat(),
            "hours_updated": next(iter(hours.values()))["資料集更新時間"] if hours else "",
            "missing": missing}
    (WEB_DATA / "facilities.json").write_text(
        json.dumps({"meta": meta, "facilities": facs}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    from collections import Counter
    print("設施數：", dict(Counter(f["cat"] for f in facs)))
    print("約略位置（路段）：", sum(1 for f in facs if f.get("precision") == "road"))
    print("定位失敗：", len(missing))
    for m in missing:
        print("  -", m)


if __name__ == "__main__":
    build()
