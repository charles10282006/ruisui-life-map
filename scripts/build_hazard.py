"""C 防災安全路線：路網＋土石流影響範圍＋避難所 → web/data/hazard.json

來源：
  路網           data/graph_drive.pkl（build_access.py 已下載的 OSM 開車路網）
  土石流影響範圍  農業部農村發展及水土保持署「115年度1753條土石流潛勢溪流影響範圍圖」（data.gov.tw 176526）
  避難收容處所    花蓮縣政府「避難收容所位置及收容人數」（data.gov.tw 159491，2026-09）為主，
                  內政部消防署「避難收容處所點位檔」（data.gov.tw 73242）補充縣府版沒有的處所
  座標校正        data/shelter_overrides.csv（名稱,lat,lon,備註）
不含：淹水潛勢（未找到花蓮縣公開下載檔）。
個資：不輸出管理人、聯絡人姓名、手機、Email；只保留場所名稱、地址、收容人數、辦公室市話。

每年更新一次（土石流圖資每年初公布新年度版本）。
"""
import csv
import json
import math
import pickle
import re
import shapefile
import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import transform as shp_transform
from shapely.strtree import STRtree

from common import RAW, DATA, WEB_DATA, TODAY

H = RAW / "hazard"
OVR = DATA / "shelter_overrides.csv"
to_wgs = Transformer.from_crs(3826, 4326, always_xy=True).transform


def area():
    v = gpd.read_file(next((RAW / "village_boundary").glob("VILLAGE_NLSC_*.shp")))
    rs = v[(v.COUNTYNAME == "花蓮縣") & (v.TOWNNAME == "瑞穗鄉")]
    a = rs.union_all()
    buf = gpd.GeoSeries([a], crs=rs.crs).to_crs(3826).buffer(1500).to_crs(4326).iloc[0]
    return rs, a, buf


def load_debris(buf):
    shp = next((H / "debris115").glob("*.shp"))
    r = shapefile.Reader(str(shp), encoding="utf-8", encodingErrors="replace")
    out = []
    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict()
        g = shp_transform(to_wgs, shape(sr.shape.__geo_interface__))
        if not g.intersects(buf):
            continue
        out.append({"no": rec["Debrisno"], "town": rec["Town"], "vill": rec["Vill"], "risk": rec["Risk"],
                    "hh": rec["Total_Res"], "old": rec["Dbno_old"], "geom": g})
    return out


def clean(s):
    return re.sub(r"\s+", "", (s or "").replace("&amp;", "&"))


def load_shelters(rs_area, buf):
    ovr = {}
    if OVR.exists():
        ovr = {r["名稱"]: r for r in csv.DictReader(open(OVR, encoding="utf-8-sig"))}
    out = {}
    for r in csv.DictReader(open(H / "shelters_hualien.csv", encoding="utf-8-sig")):
        try:
            lat, lon = float(r["避難收容所地址(緯度)"]), float(r["避難收容所地址(經度)"])
        except ValueError:
            continue
        if not buf.contains(Point(lon, lat)) or r["使用情形"] != "使用中":
            continue
        name = clean(r["避難收容所"])
        cap = sum(int(x) for x in [r["容納人數(室內人數)"], r["容納人數(室外人數)"]] if str(x).isdigit())
        out[name] = {"name": name, "vill": r["避難收容所地址(村里)"],
                     "addr": r["避難收容所地址(鄉鎮市區)"] + r["避難收容所地址(村里)"] + clean(r["避難收容所地址(街路門牌)"]),
                     "lat": lat, "lon": lon, "types": [t for t in r["適用災害"].split(",") if t],
                     "indoor": int(r["容納人數(室內人數)"]) if r["容納人數(室內人數)"].isdigit() else None, "cap": cap,
                     "weak": r["適合避難弱者安置"] == "適合", "tel": clean(r["管理人市話"]),
                     "facility": clean(r["設施設備情形"]), "src": "花蓮縣政府"}
    for r in csv.DictReader(open(H / "shelters_nfa.csv", encoding="utf-8-sig")):
        try:
            lat, lon = float(r["緯度"]), float(r["經度"])
        except ValueError:
            continue
        if not buf.contains(Point(lon, lat)):
            continue
        name = clean(r["避難收容處所名稱"])
        if any(name[:5] in k or k[:5] in name for k in out):  # 縣府版已有
            continue
        out[name] = {"name": name, "vill": r["村里"], "addr": r["縣市及鄉鎮市區"].replace("花蓮縣", "") + r["村里"] + clean(r["避難收容處所地址"]),
                     "lat": lat, "lon": lon, "types": [t for t in r["適用災害類別"].split(",") if t],
                     "indoor": None, "cap": int(r["預計收容人數"]) if r["預計收容人數"].isdigit() else None,
                     "weak": r["適合避難弱者安置"] == "是", "tel": clean(r["管理人電話"]) if clean(r["管理人電話"]).startswith("03") else "",
                     "facility": "", "src": "內政部消防署"}
    for name, o in ovr.items():
        if name in out and o.get("lat"):
            out[name].update(lat=float(o["lat"]), lon=float(o["lon"]), fixed=o.get("備註", "座標經校正"))
    return list(out.values())


def main():
    rs, a, buf = area()
    G = pickle.loads((DATA / "graph_drive.pkl").read_bytes())
    keep = [n for n, d in G.nodes(data=True) if buf.contains(Point(d["x"], d["y"]))]
    idx = {n: i for i, n in enumerate(keep)}
    debris = load_debris(buf)
    tree = STRtree([d["geom"] for d in debris])

    nodes = [[round(G.nodes[n]["y"], 6), round(G.nodes[n]["x"], 6)] for n in keep]
    edges, seen = [], set()
    SPEED = {"trunk": 55, "primary": 50, "secondary": 45, "tertiary": 35, "unclassified": 25, "residential": 20, "service": 15}
    for u, v, k, d in G.edges(keys=True, data=True):
        if u not in idx or v not in idx:
            continue
        key = tuple(sorted((u, v)))
        if key in seen:  # 防災路線視為雙向（緊急時逆向單行道也比留在原地好；單行道在瑞穗很少）
            continue
        seen.add(key)
        geom = d.get("geometry") or LineString([(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])])
        hw = d.get("highway"); hw = hw[0] if isinstance(hw, list) else hw
        kmh = SPEED.get(str(hw).replace("_link", ""), 30)
        hits = [debris[i]["no"] for i in tree.query(geom, predicate="intersects")]
        coords = [[round(y, 5), round(x, 5)] for x, y in geom.coords]
        edges.append([idx[u], idx[v], round(d["length"]), round(d["length"] / (kmh / 3.6)), hits, coords])

    shelters = load_shelters(a, buf)
    import numpy as np
    arr = np.array(nodes)
    for s in shelters:
        dd = (arr[:, 0] - s["lat"]) ** 2 + ((arr[:, 1] - s["lon"]) * 0.917) ** 2
        s["node"] = int(dd.argmin())
        s["snap_m"] = round(math.sqrt(float(dd.min())) * 111000)
        s["in_debris"] = [d["no"] for d in debris if d["geom"].contains(Point(s["lon"], s["lat"]))]
    # 離路網超過 400 公尺：多半是路網範圍外（如萬榮山區）或座標有誤，不列入路線計算
    far = [s for s in shelters if s["snap_m"] > 400]
    shelters = [s for s in shelters if s["snap_m"] <= 400]
    for s in far:
        print("  排除（離路網太遠）：", s["name"], s["snap_m"], "m")

    deb_out = [{"no": d["no"], "town": d["town"], "vill": d["vill"], "risk": d["risk"], "hh": d["hh"], "old": d["old"],
                "poly": [[[round(y, 5), round(x, 5)] for x, y in ring.exterior.coords]
                         for ring in (d["geom"].geoms if d["geom"].geom_type == "MultiPolygon" else [d["geom"]])]}
               for d in debris]
    out = {"built": TODAY.isoformat(), "nodes": nodes, "edges": edges, "shelters": shelters, "debris": deb_out}
    (WEB_DATA / "hazard.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if not OVR.exists():
        OVR.write_text("名稱,lat,lon,備註\n", encoding="utf-8-sig")
    print("節點", len(nodes), "路段", len(edges), "經過土石流範圍的路段", sum(1 for e in edges if e[4]))
    print("土石流影響範圍", len(deb_out), [d["no"] for d in deb_out])
    print("避難所", len(shelters))
    for s in shelters:
        print(" ", s["name"], s["vill"], s["src"], s["types"], s["cap"], "離路", s["snap_m"], "m", "位在土石流範圍" if s["in_debris"] else "")


if __name__ == "__main__":
    main()
