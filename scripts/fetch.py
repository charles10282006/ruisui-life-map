"""瑞穗生活地圖：下載原始資料。

用法：
  python -I scripts/fetch.py daily     # 看診時段、AED（每天）
  python -I scripts/fetch.py monthly   # 院所名單、長照據點、村里人口（每月）
  python -I scripts/fetch.py static    # 村界、OSM 路網與車站（每年）
  python -I scripts/fetch.py all

所有檔案存到 data/raw/，同時在 data/raw/fetch_log.json 記錄下載時間與來源。
"""
import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

import ssl

import requests
from requests.adapters import HTTPAdapter


class _GovTLS(HTTPAdapter):
    """部分政府網站憑證缺 Subject Key Identifier，Python 3.13+ 的嚴格模式會拒絕。
    仍然驗證憑證鏈與主機名稱，只關掉 VERIFY_X509_STRICT。"""

    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)


_s = requests.Session()
_s.mount("https://", _GovTLS())
requests.get, requests.post = _s.get, _s.post

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "ruisui-access-map/0.1 (personal non-commercial resident map)"}
NHI = "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId="

SOURCES = {
    "daily": {
        "nhi_hours.csv": NHI + "A21030000I-D21006-001",  # 健保署 特約院所固定服務時段
        "aed.csv": "https://tw-aed.mohw.gov.tw/openData?t=csv",  # 衛福部 全國AED位置
    },
    "monthly": {
        "nhi_clinic.csv": NHI + "A21030000I-D21004-009",  # 健保特約醫事機構-診所
        "nhi_pharmacy.csv": NHI + "A21030000I-D21005-001",  # 健保特約醫事機構-藥局
        "nhi_hosp_district.csv": NHI + "A21030000I-D21003-003",  # 地區醫院
        "nhi_hosp_regional.csv": NHI + "A21030000I-D21002-005",  # 區域醫院
        "nhi_hosp_center.csv": NHI + "A21030000I-D21001-003",  # 醫學中心
        "ltc_abc.csv": "https://ltcpap.mohw.gov.tw/publish/abc.csv",  # 衛福部 長照ABC據點
    },
}

# 研究範圍：瑞穗鄉與鄰近鄉鎮（找「最近的醫院」要能開到花蓮市）
BBOX = dict(north=24.02, south=23.28, east=121.66, west=121.18)
RUISUI_BBOX = (23.38, 121.25, 23.65, 121.52)  # south, west, north, east


def log(entry):
    p = RAW / "fetch_log.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data.update(entry)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def get(url, dest, timeout=300):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    if len(r.content) < 200:
        raise RuntimeError(f"{dest.name}: 回傳內容太短，可能是錯誤訊息：{r.text[:100]}")
    dest.write_bytes(r.content)
    log({dest.name: {"url": url, "fetched": dt.datetime.now().isoformat(timespec="seconds"),
                     "bytes": len(r.content)}})
    print(f"OK {dest.name} {len(r.content):,} bytes")


def fetch_population():
    """戶政司 ODRP014 村里戶數、單一年齡人口：往回找最新有資料的月份。"""
    today = dt.date.today()
    y, m = today.year - 1911, today.month
    for _ in range(6):
        ym = f"{y}{m:02d}"
        url = f"https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{ym}"
        r = requests.get(url, params={"page": 1}, headers=UA, timeout=60).json()
        if r.get("responseCode") == "OD-0101-S":
            rows, pages = [], int(r["totalPage"])
            for p in range(1, pages + 1):
                rp = r if p == 1 else requests.get(url, params={"page": p}, headers=UA, timeout=60).json()
                rows += [x for x in rp["responseData"] if x["site_id"] == "花蓮縣瑞穗鄉"]
            out = RAW / "population_village.json"
            out.write_text(json.dumps({"yyymm": ym, "rows": rows}, ensure_ascii=False), encoding="utf-8")
            log({out.name: {"url": url, "yyymm": ym, "rows": len(rows),
                            "fetched": dt.datetime.now().isoformat(timespec="seconds")}})
            print(f"OK 人口 {ym}：瑞穗鄉 {len(rows)} 村")
            return
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    raise RuntimeError("近 6 個月都查無村里人口資料")


def fetch_static():
    # 村界（國土測繪中心，TWD97 經緯度）
    z = RAW / "village_boundary.zip"
    if not z.exists():
        meta = requests.get("https://data.gov.tw/api/v2/rest/dataset/7438", timeout=60).json()["result"]
        get(meta["distribution"][0]["resourceDownloadUrl"], z, timeout=600)
    with zipfile.ZipFile(z) as f:
        f.extractall(RAW / "village_boundary")

    # OSM 開車路網
    import osmnx as ox
    ox.settings.useful_tags_way = ["highway", "name", "ref", "maxspeed", "oneway"]
    ox.settings.requests_timeout = 300
    G, last = None, None
    for url in ("https://overpass-api.de/api", "https://overpass.kumi.systems/api",
                "https://overpass.private.coffee/api"):
        ox.settings.overpass_url = url
        try:
            G = ox.graph_from_bbox((BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"]),
                                   network_type="drive", simplify=True)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  路網 {url} 失敗：{str(e)[:80]}，換下一個伺服器")
    if G is None:
        raise last
    ox.save_graphml(G, RAW / "road_drive.graphml")
    log({"road_drive.graphml": {"source": "OpenStreetMap via osmnx", "nodes": len(G.nodes),
                                "fetched": dt.datetime.now().isoformat(timespec="seconds")}})
    print(f"OK 路網 {len(G.nodes):,} 節點")

    # OSM 火車站
    s, w, n, e = RUISUI_BBOX
    q = f'[out:json][timeout:120];nwr["railway"="station"]({s},{w},{n},{e});out center tags;'
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, headers=UA, timeout=150)
    (RAW / "osm_stations.json").write_text(r.text, encoding="utf-8")
    print("OK 車站")


def safe(fn, *a, name=""):
    """單一來源失敗不影響其他來源；失敗時保留上次的檔案，並記到 fetch_log。"""
    try:
        fn(*a)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {type(e).__name__} {str(e)[:150]}")
        log({f"FAIL:{name}": {"error": str(e)[:300],
                              "at": dt.datetime.now().isoformat(timespec="seconds")}})


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode in ("daily", "all"):
        for name, url in SOURCES["daily"].items():
            safe(get, url, RAW / name, name=name)
    if mode in ("monthly", "all"):
        for name, url in SOURCES["monthly"].items():
            safe(get, url, RAW / name, name=name)
        safe(fetch_population, name="population")
    if mode in ("static", "all"):
        safe(fetch_static, name="static")
