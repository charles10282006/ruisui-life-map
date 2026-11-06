"""計算瑞穗每個道路點到各類設施的開車／步行時間 → web/data/access.json、roads.json、villages.json

方法：
  - OpenStreetMap 路網（osmnx），開車速度依道路等級假設（見 SPEEDS），不含塞車、紅綠燈、找停車位。
  - 起點 = 瑞穗鄉境內的道路節點（不是每一戶的門口）。
  - 每個設施從最近的道路節點起算，在反向路網跑 Dijkstra，得到「從任一點開到該設施」的分鐘數。
  - 步行（到車站、客運站牌）用不分方向的路網，時速 4 公里。
路網每年更新一次即可；設施清單（facilities.json）每天更新，但設施位置變動時要重跑本程式。
"""
import json
import math
import pickle
import networkx as nx
import osmnx as ox
import geopandas as gpd
from shapely.geometry import Point

from common import RAW, DATA, WEB_DATA, NET_BBOX, OVERPASS_URLS, TODAY

ox.settings.use_cache = True
ox.settings.cache_folder = str(DATA / "osm_cache")
ox.settings.requests_timeout = 300
ox.settings.overpass_url = OVERPASS_URLS[0]

# 道路等級假設時速（km/h）：鄉間道路保守估計
SPEEDS = {"motorway": 90, "trunk": 55, "trunk_link": 40, "primary": 50, "primary_link": 35,
          "secondary": 50, "secondary_link": 35, "tertiary": 40, "tertiary_link": 30,
          "unclassified": 30, "residential": 25, "living_street": 15, "service": 15, "track": 15}
WALK_M_PER_MIN = 4000 / 60

# 類別：(設施篩選, 最多算幾分鐘)
CATS = {
    "clinic": (lambda f: f["cat"] == "clinic", 60),
    "pharmacy": (lambda f: f["cat"] == "pharmacy", 60),
    "dental": (lambda f: f["cat"] == "dental", 60),
    "tcm": (lambda f: f["cat"] == "tcm", 60),
    "er": (lambda f: f["cat"] == "hospital" and f.get("er"), 120),
    "aed": (lambda f: f["cat"] == "aed", 45),
}
WALK_CATS = {
    "station": (lambda f: f["cat"] == "station", 4000),  # 公尺
    "bus": (lambda f: f["cat"] == "bus", 2500),
}
TOP_K = 3


def ruisui_area():
    v = gpd.read_file(next((RAW / "village_boundary").glob("VILLAGE_NLSC_*.shp")))
    r = v[(v.COUNTYNAME == "花蓮縣") & (v.TOWNNAME == "瑞穗鄉")].copy()
    return r


def load_graph(kind, bbox):
    path = DATA / f"graph_{kind}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    s, w, n, e = bbox
    last = None
    for url in OVERPASS_URLS:
        ox.settings.overpass_url = url
        try:
            if kind == "drive":
                G = ox.graph_from_bbox((w, s, e, n), network_type="drive", simplify=True)
            else:
                G = ox.graph_from_bbox((w, s, e, n), network_type="walk", simplify=True)
            break
        except Exception as ex:
            last = ex
    else:
        raise last
    path.write_bytes(pickle.dumps(G))
    return G


def top_k_merge(store, node, fidx, val):
    lst = store.setdefault(node, [])
    lst.append((val, fidx))
    lst.sort()
    del lst[TOP_K:]


def main():
    facs = json.loads((WEB_DATA / "facilities.json").read_text(encoding="utf-8"))["facilities"]
    vil = ruisui_area()
    area = vil.union_all()
    area_buf = gpd.GeoSeries([area], crs=vil.crs).to_crs(3826).buffer(300).to_crs(4326).iloc[0]

    # ---- 開車 ----
    G = load_graph("drive", NET_BBOX)
    G = ox.routing.add_edge_speeds(G, hwy_speeds=SPEEDS, fallback=30)
    G = ox.routing.add_edge_travel_times(G)
    Grev = G.reverse(copy=False)
    print("開車路網節點：", G.number_of_nodes())

    origins = [n for n, d in G.nodes(data=True) if area_buf.contains(Point(d["x"], d["y"]))]
    oset = set(origins)
    print("瑞穗境內道路點：", len(origins))

    result = {c: {} for c in list(CATS) + list(WALK_CATS)}
    fac_nodes = {}
    s0, w0, n0, e0 = NET_BBOX
    in_net = lambda f: s0 + 0.01 <= f["lat"] <= n0 - 0.01 and w0 + 0.01 <= f["lon"] <= e0 - 0.01
    for cat, (flt, max_min) in CATS.items():
        # 只算路網範圍內的設施（範圍外的會被吸附到路網邊緣，時間會算錯）
        idxs = [i for i, f in enumerate(facs) if flt(f) and in_net(f)]
        if not idxs:
            continue
        nodes = ox.distance.nearest_nodes(G, [facs[i]["lon"] for i in idxs], [facs[i]["lat"] for i in idxs])
        for i, nd in zip(idxs, nodes):
            fac_nodes[i] = int(nd)
            dist = nx.single_source_dijkstra_path_length(Grev, nd, cutoff=max_min * 60, weight="travel_time")
            for o, sec in dist.items():
                if o in oset:
                    # 用設施代碼（不是清單序號），設施清單每天重建也對得上
                    top_k_merge(result[cat], o, facs[i]["id"], sec / 60)
        print(f"  {cat}: {len(idxs)} 處設施")

    # ---- 步行（車站、客運站牌）----
    s, w, n, e = area_buf.bounds[1], area_buf.bounds[0], area_buf.bounds[3], area_buf.bounds[2]
    W = load_graph("walk", (s - 0.03, w - 0.03, n + 0.03, e + 0.03)).to_undirected()
    print("步行路網節點：", W.number_of_nodes())
    o_walk = ox.distance.nearest_nodes(W, [G.nodes[o]["x"] for o in origins], [G.nodes[o]["y"] for o in origins])
    walk_of = dict(zip(origins, [int(x) for x in o_walk]))
    inv = {}
    for o, wn in walk_of.items():
        inv.setdefault(wn, []).append(o)
    wb = (s - 0.03, w - 0.03, n + 0.03, e + 0.03)
    for cat, (flt, max_m) in WALK_CATS.items():
        idxs = [i for i, f in enumerate(facs) if flt(f) and wb[0] <= f["lat"] <= wb[2] and wb[1] <= f["lon"] <= wb[3]]
        if not idxs:
            continue
        nodes = ox.distance.nearest_nodes(W, [facs[i]["lon"] for i in idxs], [facs[i]["lat"] for i in idxs])
        for i, nd in zip(idxs, nodes):
            dist = nx.single_source_dijkstra_path_length(W, nd, cutoff=max_m, weight="length")
            for wn, m in dist.items():
                for o in inv.get(wn, []):
                    top_k_merge(result[cat], o, facs[i]["id"], m / WALK_M_PER_MIN)
        print(f"  {cat}: {len(idxs)} 處（步行）")

    # ---- 輸出：每個道路點 ----
    cats = list(CATS) + list(WALK_CATS)
    pts = []
    for o in origins:
        d = G.nodes[o]
        row = [round(d["y"], 5), round(d["x"], 5)]
        for c in cats:
            row.append([[fi, round(m, 1)] for m, fi in result[c].get(o, [])])
        pts.append(row)
    (WEB_DATA / "access.json").write_text(json.dumps(
        {"built": TODAY.isoformat(), "cats": cats, "speeds": SPEEDS, "points": pts},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- 輸出：道路著色（每段路取兩端較近的分鐘數）----
    feats = []
    for u, v, k, data in G.edges(keys=True, data=True):
        if u not in oset or v not in oset or k != 0:
            continue
        geom = data.get("geometry")
        coords = list(geom.coords) if geom is not None else [(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])]
        mins = {}
        for c in ("clinic", "er", "aed", "pharmacy"):
            a = result[c].get(u, [[math.inf]])[0][0]
            b = result[c].get(v, [[math.inf]])[0][0]
            m = min(a, b)
            mins[c] = None if math.isinf(m) else round(m, 1)
        feats.append({"c": [[round(x, 5), round(y, 5)] for x, y in coords], "m": mins})
    (WEB_DATA / "roads.json").write_text(json.dumps(feats, separators=(",", ":")), encoding="utf-8")

    # ---- 輸出：各村摘要（以道路點計，不是以人口計）----
    vfeats = []
    for _, r in vil.iterrows():
        geom = r.geometry
        inside = [o for o in origins if geom.contains(Point(G.nodes[o]["x"], G.nodes[o]["y"]))]
        stat = {}
        for c in ("clinic", "er", "aed", "pharmacy"):
            vals = sorted(result[c][o][0][0] for o in inside if o in result[c])
            if vals:
                stat[c] = {"median": round(vals[len(vals) // 2], 1), "p90": round(vals[int(len(vals) * 0.9) - 1], 1),
                           "over15": round(sum(v > 15 for v in vals) / len(vals), 2)}
        simple = geom.simplify(0.0003)
        vfeats.append({"type": "Feature", "properties": {"name": r.VILLNAME, "code": r.VILLCODE, "n": len(inside), **stat},
                       "geometry": json.loads(gpd.GeoSeries([simple]).to_json())["features"][0]["geometry"]})
    (WEB_DATA / "villages.json").write_text(json.dumps({"type": "FeatureCollection", "features": vfeats},
                                                       ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for f in vfeats:
        p = f["properties"]
        print(p["name"], "道路點", p["n"], "診所中位數", p.get("clinic", {}).get("median"), "急診中位數", p.get("er", {}).get("median"))


if __name__ == "__main__":
    main()
