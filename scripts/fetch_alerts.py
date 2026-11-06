"""C 防災安全路線：即時警戒 → web/data/alerts.json

  土石流／大規模崩塌警戒  農業部農村發展及水土保持署（data.gov.tw 148402、7284，免金鑰；只有發布時才有資料）
  公路即時事件（含封閉）  交通部 TDX 公路局 LiveEvent（需 TDX 金鑰）
  天氣特報              氣象署 W-C0033-001（需 CWA 授權碼）
平時每 3 小時、颱風豪雨期間建議每 30 分鐘執行一次。任一來源失敗時，該項保留上次結果並標示。
"""
import json
import re
import ssl
import sys
import time
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter

from common import WEB_DATA, UA, load_key

AREA_TOWNS = {"瑞穗鄉", "光復鄉", "玉里鎮", "萬榮鄉", "卓溪鄉", "豐濱鄉"}
BBOX = (23.38, 121.28, 23.66, 121.50)  # 南, 西, 北, 東：瑞穗與周邊路段
# 「道路不通」才從路網移除；只封單一車道、調撥通行者列為「注意」（例：台9線台鐵施工南向車道封閉、調撥雙向通行）
BLOCK_WORDS = ("阻斷", "中斷", "雙向封閉", "全線封閉", "道路封閉", "禁止通行", "坍方", "崩塌", "路基流失", "淹水")
CAUTION_WORDS = ("封閉", "管制", "落石", "土石", "改道", "調撥", "施工")


class Lenient(HTTPAdapter):
    """Python 3.14 對部分政府網站憑證過嚴（缺 Subject Key Identifier）；仍驗證憑證鏈。"""
    def init_poolmanager(self, *a, **k):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        k["ssl_context"] = ctx
        return super().init_poolmanager(*a, **k)


S = requests.Session()
S.mount("https://", Lenient())
S.headers["User-Agent"] = UA


def get_json(url, **kw):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=60, **kw)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def debris_alerts():
    out = []
    for url in ["https://data.moa.gov.tw/Service/OpenData/TransService.aspx?UnitId=ofvJwoWC5wum&IsTransData=1",
                "https://data.moa.gov.tw/Service/OpenData/FromM/GetCustomerDebrisAlertInfo.aspx?IsTransData=1&UnitId=018"]:
        for a in get_json(url) or []:
            town = a.get("Town") or a.get("town") or ""
            if town not in AREA_TOWNS:
                continue
            lvl = (a.get("AlertLevel") or "").lower()
            out.append({"type": a.get("AlertType"), "no": a.get("DebrisNo"), "town": town, "vill": a.get("Vill"),
                        "level": "red" if lvl.startswith("r") else "yellow" if lvl.startswith("y") else lvl,
                        "name": a.get("LandslideName"), "updated": a.get("LastUpdateDate")})
    uniq = {(x["no"], x["vill"], x["level"]): x for x in out}
    return list(uniq.values())


def parse_points(wkt):
    nums = re.findall(r"(-?\d+\.\d+)\s+(-?\d+\.\d+)", wkt or "")
    return [[float(b), float(a)] for a, b in nums]  # WKT 是 經度 緯度 → [lat, lon]


def road_events():
    cid, sec = load_key("TDX_CLIENT_ID"), load_key("TDX_CLIENT_SECRET")
    if not cid:
        raise RuntimeError("沒有 TDX 金鑰")
    tok = S.post("https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token",
                 data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}, timeout=60).json()["access_token"]
    js = get_json("https://tdx.transportdata.tw/api/basic/v1/Traffic/RoadEvent/LiveEvent/Highway",
                  params={"$format": "JSON"}, headers={"Authorization": f"Bearer {tok}"})
    s, w, n, e = BBOX
    out = []
    for ev in js.get("LiveEvents", []):
        pts = parse_points(ev.get("Positions"))
        if not any(s <= la <= n and w <= lo <= e for la, lo in pts):
            continue
        text = (ev.get("EventTitle") or "") + (ev.get("Description") or "")
        out.append({"id": ev.get("EventID"), "title": ev.get("EventTitle"), "desc": ev.get("Description"),
                    "pts": pts,
                    "closure": any(k in text for k in BLOCK_WORDS),
                    "caution": any(k in text for k in CAUTION_WORDS),
                    "start": ev.get("EffectiveTime"), "end": ev.get("ExpireTime") or ev.get("EndTime")})
    return out


def weather_warnings():
    key = load_key("CWA_API_KEY")
    if not key:
        raise RuntimeError("沒有 CWA 授權碼")
    js = get_json("https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0033-001",
                  params={"Authorization": key, "format": "JSON", "locationName": "花蓮縣"})
    out = []
    for loc in js.get("records", {}).get("location", []):
        for h in loc.get("hazardConditions", {}).get("hazards", []) or []:
            info, vt = h.get("info", {}), h.get("validTime", {})
            out.append({"phenomena": info.get("phenomena"), "significance": info.get("significance"),
                        "start": vt.get("startTime"), "end": vt.get("endTime")})
    return out


def main():
    path = WEB_DATA / "alerts.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = {"updated": datetime.now().isoformat(timespec="minutes"), "errors": []}
    for k, fn in [("debris", debris_alerts), ("roads", road_events), ("weather", weather_warnings)]:
        try:
            data[k] = fn()
        except Exception as ex:
            data[k] = old.get(k, [])
            data["errors"].append(f"{k}：{type(ex).__name__}")
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("土石流警戒", len(data["debris"]), "道路事件", len(data["roads"]),
          "（封閉類", sum(1 for r in data["roads"] if r["closure"]), "）天氣特報", len(data["weather"]), "錯誤", data["errors"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
