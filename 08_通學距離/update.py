"""瑞穗生活地圖 #8 通學距離：每年重跑一次的更新程式。

用法（在本資料夾執行）：
    python update.py            # 下載教育部資料＋計算＋產生 index.html（路網已存在就沿用）
    python update.py --osm      # 另外重新下載 OSM 路網／學校點位（較慢）
    python update.py --check    # 只做檢查：學區表文字、學校是否都比對到

流程：
  1 教育部統計處：國小／國中校別資料（學生數）、國中名錄（地址）→ raw/
  2 花蓮縣 115 學年度學區劃分表 PDF → raw/（比對文字，確認沒被改版）
  3 OSM：開車／步行路網、學校點位 → raw/（fetch_osm.py）
  4 各村代表點（村內路口最密處）
  5 Dijkstra：村 → 各校 的道路距離、開車分鐘、步行分鐘
  6 寫 data/result.json，並把資料嵌入 template.html → index.html
"""
import csv, io, json, ssl, sys, re, math
from pathlib import Path
from datetime import date

import requests
from requests.adapters import HTTPAdapter

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
DATA = HERE / "data"
ROOT = HERE.parent  # 瑞穗生活地圖
VILLAGE_SHP = ROOT / "data" / "raw" / "village_boundary" / "VILLAGE_NLSC_1150817.shp"
POP_JSON = ROOT / "data" / "raw" / "population_village.json"
RAW.mkdir(exist_ok=True); DATA.mkdir(exist_ok=True)

# ---------------------------------------------------------------- 設定
# 開車速度假設（km/h）：依 OSM highway 等級，不採用 OSM maxspeed（多數缺漏）。
DRIVE_SPEEDS = {
    "motorway": 80, "trunk": 60, "primary": 50, "secondary": 45, "tertiary": 40,
    "unclassified": 35, "residential": 30, "living_street": 20, "service": 20, "track": 15,
}
DRIVE_DEFAULT = 30
WALK_KMH = 4.0  # 國小學童步行速度假設

# 本頁收錄的學校。key＝顯示名稱。
#   moe：教育部統計處校別資料中的學校名稱（取學生數）；osm：OSM 名稱候選
#   addr：官方地址（來源 addr_src）；沒有就留 None（頁面會標示「僅 OSM 位置」）
SCHOOLS = [
    # 瑞穗鄉國小
    dict(key="瑞穗國小", level="國小", town="瑞穗鄉", moe="縣立瑞穗國小", osm=["瑞穗國小", "瑞穗國民小學"],
         addr="花蓮縣瑞穗鄉溫泉路一段19號", addr_src="學校名錄轉載（edu099.com），學校官網 zshps.hlc.edu.tw"),
    dict(key="瑞北國小", level="國小", town="瑞穗鄉", moe="縣立瑞北國小", osm=["瑞北國小", "瑞北國民小學"],
         addr="花蓮縣瑞穗鄉中正北路二段35號", addr_src="瑞北國小官網頁尾"),
    dict(key="瑞美國小", level="國小", town="瑞穗鄉", moe="縣立瑞美國小", osm=["瑞美國小", "瑞美國民小學"], addr=None),
    dict(key="鶴岡國小", level="國小", town="瑞穗鄉", moe="縣立鶴岡國小", osm=["鶴岡國小", "鶴岡國民小學"],
         addr="花蓮縣瑞穗鄉鶴岡村興鶴路二段167號", addr_src="鶴岡國小官網頁尾（hgps.hlc.edu.tw）"),
    dict(key="舞鶴國小", level="國小", town="瑞穗鄉", moe="縣立舞鶴國小", osm=["舞鶴國小", "舞鶴國民小學"], addr=None),
    dict(key="富源國小", level="國小", town="瑞穗鄉", moe="縣立富源國小", osm=["富源國小", "富源國民小學"],
         addr="花蓮縣瑞穗鄉富源村學士路30號", addr_src="富源國小官網頁尾（fyps.hlc.edu.tw）"),
    dict(key="奇美國小", level="國小", town="瑞穗鄉", moe="縣立奇美國小", osm=["奇美國小", "奇美國民小學"], addr=None),
    # 國中（地址一律取自教育部名錄 j1_new.csv）
    dict(key="瑞穗國中", level="國中", town="瑞穗鄉", moe="縣立瑞穗國中", osm=["瑞穗國中", "瑞穗國民中學"]),
    dict(key="富源國中", level="國中", town="光復鄉", moe="縣立富源國中", osm=["富源國中", "富源國民中學"],
         note="校址在光復鄉大富村；瑞穗鄉富源村、富民村、富興村屬其學區"),
    dict(key="三民國中", level="國中", town="玉里鎮", moe="縣立三民國中", osm=["三民國中", "三民國民中學"],
         note="舞鶴村 12-13 鄰與瑞穗國中同為自由學區"),
    dict(key="玉東國中", level="國中", town="玉里鎮", moe="縣立玉東國中", osm=["玉東國中", "玉東國民中學"],
         note="玉里鎮德武里與瑞穗國中同為自由學區"),
    dict(key="玉里國中", level="國中", town="玉里鎮", moe="縣立玉里國中", osm=["玉里國中", "玉里國民中學"]),
    dict(key="光復國中", level="國中", town="光復鄉", moe="縣立光復國中", osm=["光復國中", "光復國民中學"]),
    dict(key="鳳林國中", level="國中", town="鳳林鎮", moe="縣立鳳林國中", osm=["鳳林國中", "鳳林國民中學"]),
    # 高中職（教育部未提供校別學生數的開放檔，學生數留空）
    dict(key="玉里高中", level="高中職", town="玉里鎮", moe=None, osm=["國立玉里高級中學", "玉里高中"],
         addr="花蓮縣玉里鎮中華路424號", addr_src="玉里高中學校文件（ylsh.hlc.edu.tw）"),
    dict(key="光復商工", level="高中職", town="光復鄉", moe=None, osm=["國立光復高級商工職業學校", "光復商工"],
         addr="花蓮縣光復鄉林森路100號", addr_src="光復商工官網（kfcivs.hlc.edu.tw）"),
]
# 為何列入「鄰近鄉鎮」學校：
WHY = {
    "富源國中": "學區表：瑞穗鄉富源村、富民村、富興村的國中學區",
    "三民國中": "學區表：舞鶴村 12-13 鄰與瑞穗國中同為自由學區",
    "玉東國中": "學區表：玉里鎮德武里與瑞穗國中同為自由學區（瑞穗鄉居民不在其學區內，列為鄰近學校）",
    "玉里國中": "玉里鎮最大的鄰近國中，瑞穗鄉以南最近的一所",
    "光復國中": "瑞穗鄉以北最近的鄰近國中",
    "鳳林國中": "光復鄉以北，瑞穗往花蓮方向的鄰近國中",
    "玉里高中": "瑞穗鄉南邊最近的公立高中（普通科、技職科）",
    "光復商工": "瑞穗鄉北邊最近的公立高職",
}

# 學區：115 學年度起實施，花蓮縣政府公告。以下 value 必須能在 PDF 文字中逐字（去空白）找到，否則 update.py 會報錯。
ZONE_SRC = "花蓮縣政府教育處「115學年度起實施 花蓮縣公立國民中小學學區劃分表」（府教學字第1140213462B號，114年10月29日函）"
ZONE_CHECK = [  # (出現在 PDF 的片段)
    "瑞穗村、瑞祥村", "瑞北村1-6鄰", "瑞北村7-8鄰與瑞穗國小同為自由學區", "瑞良村、瑞美村10-17鄰",
    "瑞美村1-9鄰、18-20鄰與瑞穗國小同為自由學區", "鶴岡村。", "舞鶴村。", "奇美村。",
    "富源村、富興村、富民村", "瑞穗鄉富源村、富民村、富興村", "鶴岡村10鄰與瑞穗國中同為自由學區",
    "舞鶴村12-13鄰與三民國中同為自由學區",
]
# 各村的學區文字（逐項抄自上表；「自由學區」＝可擇一）
ZONES = {
    "瑞穗村": dict(小="瑞穗國小", 中="瑞穗國中"),
    "瑞祥村": dict(小="瑞穗國小", 中="瑞穗國中"),
    "瑞北村": dict(小="1-6 鄰：瑞北國小；7-8 鄰：瑞北國小與瑞穗國小同為自由學區", 中="瑞穗國中"),
    "瑞美村": dict(小="1-9 鄰、18-20 鄰：瑞穗國小與瑞美國小同為自由學區；10-17 鄰：瑞美國小", 中="瑞穗國中"),
    "瑞良村": dict(小="瑞美國小", 中="瑞穗國中"),
    "奇美村": dict(小="奇美國小", 中="瑞穗國中"),
    "鶴岡村": dict(小="鶴岡國小", 中="10 鄰：瑞穗國中與富源國中同為自由學區；其餘鄰：瑞穗國中"),
    "舞鶴村": dict(小="舞鶴國小", 中="12-13 鄰：瑞穗國中與三民國中同為自由學區；其餘鄰：瑞穗國中"),
    "富源村": dict(小="富源國小", 中="富源國中"),
    "富民村": dict(小="富源國小", 中="富源國中"),
    "富興村": dict(小="富源國小", 中="富源國中"),
}

SOURCES = [
    ("學校名單與國小／國中學生數", "教育部統計處「國民小學校別資料」「國民中學校別資料」，114 學年度（最新公布學年）；政府資料開放平臺，政府資料開放授權條款第1版"),
    ("國中地址", "教育部統計處「國民中學名錄」，115 學年度；政府資料開放授權條款第1版"),
    ("學區", ZONE_SRC + "；以瑞穗國小網站 2025-10-30 轉載之 PDF 取得全文"),
    ("村界", "內政部國土測繪中心「村(里)界」1150817 版；政府資料開放授權條款第1版"),
    ("村人口", "內政部戶政司 ODRP014，115 年 9 月；政府資料開放授權條款第1版"),
    ("道路與學校位置", "© OpenStreetMap contributors（ODbL）；以 OSMnx 下載"),
]


# ---------------------------------------------------------------- 工具
class _GovTLS(HTTPAdapter):
    """Python 3.14 會拒絕缺 Subject Key Identifier 的政府憑證；關閉 STRICT 旗標，仍會驗證憑證。"""
    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kw["ssl_context"] = ctx
        super().init_poolmanager(*a, **kw)


def session():
    s = requests.Session()
    s.mount("https://", _GovTLS())
    s.headers["User-Agent"] = "ruisui-life-map/0.1 (community open-data map)"
    return s


def read_csv(path):
    return list(csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8-sig"))))


# ---------------------------------------------------------------- 1 教育部資料
def fetch_moe(s):
    # 國小：依序嘗試最新學年（民國年）
    yr = date.today().year - 1911 + 1
    got = None
    for y in range(yr, yr - 4, -1):
        r = s.get(f"https://stats.moe.gov.tw/files/detail/{y}/{y}_basec.csv", timeout=60)
        if r.status_code == 200 and len(r.content) > 5000:
            (RAW / f"{y}_basec.csv").write_bytes(r.content); got = y; break
    if not got:
        raise SystemExit("找不到國小校別資料")
    r = s.get("https://stats.moe.gov.tw/files/opendata/basej.csv", timeout=120)
    (RAW / "basej_all.csv").write_bytes(r.content)
    r = s.get("https://stats.moe.gov.tw/files/opendata/j1_new.csv", timeout=120)
    (RAW / "j1_new.csv").write_bytes(r.content)
    return got


def moe_students(year_c):
    """回傳 {學校名稱: (學年, 學生數)}"""
    out = {}
    for r in read_csv(RAW / f"{year_c}_basec.csv"):
        if "花蓮" not in r["縣市名稱"]:
            continue
        n = sum(int(r[f"{g}年級{s}學生數"] or 0) for g in range(1, 7) for s in "男女")
        out[r["學校名稱"]] = (int(r["學年度"]), n)
    rows = [r for r in read_csv(RAW / "basej_all.csv") if "花蓮" in r["縣市名稱"]]
    ymax = max(int(r["學年度"]) for r in rows)
    for r in rows:
        if int(r["學年度"]) != ymax:
            continue
        n = sum(int(r[f"學生數{g}年級{s}"] or 0) for g in (7, 8, 9) for s in "男女")
        out[r["學校名稱"]] = (ymax, n)
    return out


def moe_junior_addr():
    rows = [r for r in read_csv(RAW / "j1_new.csv") if "花蓮" in r["縣市名稱"]]
    ymax = max(int(r["學年度"]) for r in rows)
    return {r["學校名稱"]: (re.sub(r"^\[\d+\]", "", r["地址"]), ymax) for r in rows if int(r["學年度"]) == ymax}


# ---------------------------------------------------------------- 2 學區檢查
def check_zone_pdf():
    t = (RAW / "zone115.txt").read_text(encoding="utf-8") if (RAW / "zone115.txt").exists() else ""
    flat = re.sub(r"\s+", "", t)
    miss = [c for c in ZONE_CHECK if c not in flat]
    return miss


# ---------------------------------------------------------------- 3-5 空間計算
def main(do_osm=False, check_only=False):
    import numpy as np
    import geopandas as gpd
    import networkx as nx
    import osmnx as ox
    from shapely.geometry import Point
    if not hasattr(np, "float_"):  # 舊版 networkx 讀 graphml 需要 np.float_（NumPy 2 已移除）
        np.float_ = np.float64

    s = session()
    if not check_only:
        try:
            year_c = fetch_moe(s)
        except Exception as e:
            print("下載教育部資料失敗，改用 raw/ 內舊檔：", e)
            year_c = max(int(p.name[:3]) for p in RAW.glob("*_basec.csv"))
    else:
        year_c = max(int(p.name[:3]) for p in RAW.glob("*_basec.csv"))
    stud = moe_students(year_c)
    jaddr = moe_junior_addr()
    miss = check_zone_pdf()
    print("學區表比對：", "全部找到" if not miss else f"找不到 {miss}（PDF 可能改版，請人工確認）")
    if do_osm:
        import fetch_osm
        fetch_osm.main(force=True)
    if check_only:
        for sc in SCHOOLS:
            if sc["moe"]:
                print(sc["key"], stud.get(sc["moe"]), jaddr.get(sc["moe"]))
        return

    # --- OSM 學校點位比對
    osm_schools = json.loads((RAW / "osm_schools.json").read_text(encoding="utf-8"))
    def find_osm(names):
        for nm in names:
            for o in osm_schools:
                if o.get("name") and (o["name"] == nm or nm in o["name"]):
                    return o
        return None
    schools = []
    for sc in SCHOOLS:
        o = find_osm(sc["osm"])
        if not o:
            print("OSM 找不到：", sc["key"]); continue
        d = dict(sc)
        d["lat"], d["lon"], d["osm_name"], d["osm_id"] = o["lat"], o["lon"], o["name"], o.get("id")
        if sc["level"] == "國中" and sc["moe"] in jaddr:
            d["addr"], yr = jaddr[sc["moe"]]
            d["addr_src"] = f"教育部統計處國中名錄，{yr} 學年度"
        d["osm_addr"] = " ".join(str(o.get(k) or "") for k in ("addr:city", "addr:district", "addr:street", "addr:housenumber")).strip() or None
        if sc["moe"] and sc["moe"] in stud:
            d["students_year"], d["students"] = stud[sc["moe"]]
        else:
            d["students_year"], d["students"] = None, None
        d["why"] = WHY.get(sc["key"])
        schools.append(d)

    # --- 路網
    Gd = ox.load_graphml(RAW / "drive.graphml")
    Gd = ox.add_edge_speeds(Gd, hwy_speeds=DRIVE_SPEEDS, fallback=DRIVE_DEFAULT)
    # add_edge_speeds 會優先採用 OSM maxspeed；這裡改成只用等級速度
    for u, v, k, dta in Gd.edges(keys=True, data=True):
        hw = dta.get("highway"); hw = hw[0] if isinstance(hw, list) else hw
        sp = DRIVE_SPEEDS.get(str(hw).replace("_link", ""), DRIVE_DEFAULT)
        dta["speed_kph"] = sp
        dta["travel_time"] = dta["length"] / (sp / 3.6)
    Gdr = Gd.reverse(copy=True)
    Gw = ox.load_graphml(RAW / "walk.graphml") if (RAW / "walk.graphml").exists() else None

    # --- 村界與代表點
    vg = gpd.read_file(VILLAGE_SHP, encoding="utf-8")
    vg = vg[vg["TOWNNAME"] == "瑞穗鄉"].to_crs(3826)
    pop = {r["district_code"]: r for r in json.loads(POP_JSON.read_text(encoding="utf-8"))["rows"]}

    # 步行路網的所有節點（路口／道路端點）：聚落的街巷多、山區與農路少，用來找「聚落最密處」
    nodes = ox.graph_to_gdfs(Gw, edges=False)[["geometry"]].to_crs(3826)
    nxy = np.c_[nodes.geometry.x, nodes.geometry.y]
    villages = []
    for _, v in vg.iterrows():
        code = v["VILLCODE"]
        name = pop[code]["village"]
        inside = nodes[nodes.within(v.geometry)]
        if len(inside) >= 5:
            pts = np.c_[inside.geometry.x, inside.geometry.y]
            d2 = ((pts[:, None, :] - nxy[None, :, :]) ** 2).sum(-1)  # 與全區節點比，避免邊界效應
            cnt = (d2 <= 200 ** 2).sum(1)
            i = int(cnt.argmax())
            rep = Point(pts[i]); how = f"村內 {len(inside)} 個路口中，200 公尺內路口最密的一處（{int(cnt[i])} 個），視為聚落中心"
        else:
            rep = v.geometry.representative_point(); how = "村內路網資料不足，改用村界內部代表點"
        repll = gpd.GeoSeries([rep], crs=3826).to_crs(4326).iloc[0]
        p = pop[code]
        age = lambda a: sum(int(p.get(f"people_age_{a:03d}_{x}", 0) or 0) for x in "mf")
        villages.append(dict(
            code=code, name=name, lat=repll.y, lon=repll.x, how=how,
            households=int(p["household_no"]), people=int(p["people_total"]),
            age6_11=sum(age(a) for a in range(6, 12)), age12_14=sum(age(a) for a in range(12, 15)),
            zone=ZONES.get(name), geom=json.loads(gpd.GeoSeries([v.geometry], crs=3826).to_crs(4326).to_json())["features"][0]["geometry"],
        ))

    # --- 最短路徑
    def nearest(G, lat, lon):
        return ox.distance.nearest_nodes(G, lon, lat)
    for sc in schools:
        sc["nd"] = nearest(Gd, sc["lat"], sc["lon"])
        if Gw: sc["nw"] = nearest(Gw, sc["lat"], sc["lon"])
    for v in villages:
        v["rows"] = []
        nd = nearest(Gd, v["lat"], v["lon"])
        # 去程（村→校）與回程（校→村）各算一次，取較短者：OSM 台9 在光復—鳳林間的單行道資料有缺口，
        # 只算單向會讓去程繞遠路 10 公里以上
        td, paths = nx.single_source_dijkstra(Gd, nd, weight="travel_time")
        tr, pathsr = nx.single_source_dijkstra(Gdr, nd, weight="travel_time")
        nw = nearest(Gw, v["lat"], v["lon"]) if Gw else None
        lw, _ = nx.single_source_dijkstra(Gw, nw, weight="length") if Gw else ({}, None)
        for sc in schools:
            row = dict(school=sc["key"])
            t, path, GG = td.get(sc["nd"]), None, Gd
            if t is not None:
                path = paths[sc["nd"]]
            t2 = tr.get(sc["nd"])
            if t2 is not None and (t is None or t2 < t):
                t, path, GG = t2, pathsr[sc["nd"]], Gdr
            if t is not None:
                L = 0.0
                for a, b in zip(path[:-1], path[1:]):
                    L += min(e["length"] for e in GG[a][b].values())
                row.update(drive_km=round(L / 1000, 1), drive_min=round(t / 60))
            if Gw and sc["nw"] in lw:
                w = lw[sc["nw"]]
                # 步行路網若繞得比開車還遠很多，多半是步行路網在該處不連通，不顯示
                if "drive_km" in row and w / 1000 > row["drive_km"] * 1.5 + 0.5:
                    w = None
            else:
                w = None
            if w is not None:
                row.update(walk_km=round(w / 1000, 1), walk_min=round(w / (WALK_KMH * 1000 / 60)))
            v["rows"].append(row)

    for sc in schools:
        sc.pop("nd", None); sc.pop("nw", None)
    out = dict(
        generated=str(date.today()), moe_year_primary=year_c, drive_speeds=DRIVE_SPEEDS, drive_default=DRIVE_DEFAULT,
        walk_kmh=WALK_KMH, zone_src=ZONE_SRC, sources=SOURCES, schools=schools, villages=villages,
    )
    (DATA / "result.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    (HERE / "index.html").write_text(tpl.replace("__DATA__", json.dumps(out, ensure_ascii=False)), encoding="utf-8")
    print("完成：", len(schools), "校，", len(villages), "村 → index.html")


if __name__ == "__main__":
    main(do_osm="--osm" in sys.argv, check_only="--check" in sys.argv)
