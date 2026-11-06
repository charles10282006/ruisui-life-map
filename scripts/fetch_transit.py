"""每日更新：就醫交通用的台鐵（TDX）與公路客運（公路局）時刻 → web/data/transit.json

台鐵：TDX DailyTrainTimetable（今天起 7 天，含加班車與停駛），需要 TDX_CLIENT_ID / TDX_CLIENT_SECRET。
客運：公路局「客運時刻」（data/raw/bus/timetable.csv，由 fetch_daily.py 下載），只取經過瑞穗的班次。
金鑰只從環境變數或 ~/.ruisui_keys.env 讀取，不印出、不寫入任何輸出檔。
"""
import csv
import json
import sys
import time
from datetime import timedelta
import requests
import geopandas as gpd
from shapely.geometry import Point

from common import RAW, WEB_DATA, TODAY, UA, load_key

TDX = "https://tdx.transportdata.tw"
# 瑞穗居民就醫會用到的車站
STATIONS = ["瑞穗", "富源", "三民", "玉里", "光復", "鳳林", "萬榮", "大富", "南平", "壽豐", "志學", "吉安", "花蓮"]
DAYS = 7


def tdx_token():
    cid, sec = load_key("TDX_CLIENT_ID"), load_key("TDX_CLIENT_SECRET")
    if not cid or not sec:
        return None
    r = requests.post(f"{TDX}/auth/realms/TDXConnect/protocol/openid-connect/token",
                      data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec},
                      headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def tdx_get(path, token, **params):
    """TDX 免費會員有每分鐘次數限制：每次間隔 2 秒，遇到 429 等待後重試。"""
    params.setdefault("$format", "JSON")
    for attempt in range(5):
        time.sleep(2)
        r = requests.get(f"{TDX}{path}", params=params,
                         headers={"Authorization": f"Bearer {token}", "User-Agent": UA}, timeout=120)
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def rail(token):
    st = tdx_get("/api/basic/v3/Rail/TRA/Station", token)
    st = st.get("Stations", st)
    name2id = {s["StationName"]["Zh_tw"]: s["StationID"] for s in st}
    ids = {name2id[n]: n for n in STATIONS if n in name2id}
    coords = {name2id[n]: [s["StationPosition"]["PositionLat"], s["StationPosition"]["PositionLon"]]
              for s in st for n in STATIONS if n in name2id and s["StationID"] == name2id[n]}
    days = {}
    for k in range(DAYS):
        d = (TODAY + timedelta(days=k)).isoformat()
        try:
            js = tdx_get(f"/api/basic/v3/Rail/TRA/DailyTrainTimetable/TrainDate/{d}", token)
        except Exception as e:  # 某一天失敗：保留已抓到的天數
            print(f"台鐵 {d} 下載失敗：{type(e).__name__}")
            continue
        trains = []
        for t in js.get("TrainTimetables", []):
            stops = [[ids[s["StationID"]], (s.get("DepartureTime") or s.get("ArrivalTime"))[:5]]
                     for s in t["StopTimes"] if s["StationID"] in ids]
            # 只保留在瑞穗或富源有停、且至少停兩個關注車站的車次
            if len(stops) >= 2 and any(s[0] in ("瑞穗", "富源") for s in stops):
                info = t["TrainInfo"]
                trains.append({"no": info["TrainNo"], "type": info["TrainTypeName"]["Zh_tw"], "s": stops})
        days[d] = trains
        print(f"台鐵 {d}: {len(trains)} 班停靠瑞穗/富源")
    return {"stations": {n: coords.get(i) for i, n in ids.items()}, "days": days}


def bus():
    v = gpd.read_file(next((RAW / "village_boundary").glob("VILLAGE_NLSC_*.shp")))
    area = v[(v.COUNTYNAME == "花蓮縣") & (v.TOWNNAME == "瑞穗鄉")].union_all()
    rows = list(csv.DictReader(open(RAW / "bus" / "timetable.csv", encoding="utf-8-sig")))
    # 找出經過瑞穗的路線
    routes = set()
    for r in rows:
        try:
            if area.contains(Point(float(r["Longitude"]), float(r["Latitude"]))):
                routes.add((r["客運業者"], r["路線編號"]))
        except ValueError:
            pass
    trips, stops = {}, {}
    for r in rows:
        if (r["客運業者"], r["路線編號"]) not in routes:
            continue
        key = (r["路線編號"], r["支線"], r["往返"], r["班次序"], r["路線名稱"])
        t = trips.setdefault(key, {"route": r["路線編號"], "name": r["路線名稱"], "op": r["客運業者"],
                                   "wd": "".join(r[f"星期{c}"] for c in "一二三四五六日"),
                                   "from": r["開班日期_起"], "to": r["開班日期_迄"], "s": []})
        hhmm = r["發車時間"].zfill(6)
        t["s"].append([r["站位ID"], f"{hhmm[:2]}:{hhmm[2:4]}"])
        stops[r["站位ID"]] = [r["站名"], round(float(r["Latitude"]), 5), round(float(r["Longitude"]), 5)]
    out = []
    for t in trips.values():
        t["s"].sort(key=lambda x: x[1])
        out.append(t)
    print(f"客運：{len(routes)} 條路線、{len(out)} 個班次、{len(stops)} 個站牌")
    return {"stops": stops, "trips": out}


def main():
    data = {"built": TODAY.isoformat(), "bus": bus()}
    try:
        tok = tdx_token()
        if tok:
            data["rail"] = rail(tok)
        else:
            print("沒有 TDX 金鑰，略過台鐵")
    except Exception as e:  # 金鑰錯誤或 TDX 異常：保留舊的台鐵資料
        print("台鐵下載失敗：", type(e).__name__, str(e)[:120])
        old = WEB_DATA / "transit.json"
        if old.exists():
            data["rail"] = json.loads(old.read_text(encoding="utf-8")).get("rail")
    (WEB_DATA / "transit.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
