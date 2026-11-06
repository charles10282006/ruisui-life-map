"""下載 OSM 開車／步行路網與學校點位，存到 raw/（已存在的檔案會略過，加 --force 重抓）。
Overpass 伺服器常因負載回 502，所以輪流嘗試多個端點。"""
import sys, time, osmnx as ox
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
BBOX = (121.25, 23.30, 121.55, 23.80)  # 西, 南, 東, 北（玉里～鳳林）
ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
ox.settings.requests_timeout = 600
ox.settings.overpass_rate_limit = False


def retry(fn, tries=9):
    last = None
    for i in range(tries):
        ox.settings.overpass_url = ENDPOINTS[i % len(ENDPOINTS)]
        try:
            return fn()
        except Exception as e:  # 502 / 逾時：換端點再試
            last = e
            print("retry", i, ox.settings.overpass_url, str(e)[:80], flush=True)
            time.sleep(10 + 5 * i)
    raise last


def main(force=False):
    RAW.mkdir(exist_ok=True)
    f = RAW / "drive.graphml"
    if force or not f.exists():
        G = retry(lambda: ox.graph_from_bbox(BBOX, network_type="drive", simplify=True))
        ox.save_graphml(G, f); print("drive", len(G.nodes), len(G.edges), flush=True)
    f = RAW / "walk.graphml"
    if force or not f.exists():
        G = retry(lambda: ox.graph_from_bbox(BBOX, network_type="walk", simplify=True))
        ox.save_graphml(G, f); print("walk", len(G.nodes), len(G.edges), flush=True)
    f = RAW / "osm_schools.json"
    if force or not f.exists():
        g = retry(lambda: ox.features_from_bbox(BBOX, tags={"amenity": ["school"]})).reset_index()
        pt = g.geometry.representative_point()
        g["lon"], g["lat"] = pt.x, pt.y
        cols = [c for c in g.columns if c not in ("geometry", "nodes", "ways")]
        g[cols].to_json(f, orient="records", force_ascii=False)
        print("schools", len(g), flush=True)


if __name__ == "__main__":
    main("--force" in sys.argv)
