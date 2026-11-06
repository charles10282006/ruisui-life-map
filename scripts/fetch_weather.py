"""B 農事天氣窗：氣象署瑞穗鄉預報 → web/data/weather.json

來源（氣象資料開放平臺，需授權碼 CWA_API_KEY）：
  F-D0047-041  鄉鎮天氣預報－花蓮縣未來3天（逐時溫濕度、逐3小時降雨機率／天氣現象、風速）
  F-D0047-043  鄉鎮天氣預報－花蓮縣未來1週（逐12小時）
  M-A0085-001  健康氣象－熱傷害指數及警示（全台各鄉鎮五日逐三小時）
氣象署一天發布 4 次預報，建議每 6 小時執行一次。

判斷門檻（本頁自訂的參考門檻，不是官方標準；噴藥風速參考農業藥物試驗所無人機施藥指引）：
  噴藥  可以：蒲福風級 ≤2、本時段降雨機率 ≤20%、下一時段 ≤30%、天氣現象沒有「雨／雷」
        不建議：風級 ≥4，或本時段降雨機率 >40%，或下一時段 >50%，或有雨／雷，或熱傷害「危險」
        其餘為「注意」；最高溫 ≥32°C 時至少「注意」（高溫藥液蒸發快）
  曬穀／曬乾（只看白天 06–18 時）
        適合：降雨機率 ≤10%、天氣現象有「晴」或「多雲」且無雨、平均濕度 ≤75%
        不建議：降雨機率 >20% 或有雨
  熱傷害：直接使用氣象署發布的熱傷害指數與警示（注意／警戒／危險），本頁不另訂門檻。
"""
import json
import sys
from datetime import datetime
import requests

from common import RAW, WEB_DATA, UA, load_key

API = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
TOWN = "瑞穗鄉"
RAIN_WORDS = ("雨", "雷")


def get(ds, key, **params):
    import time
    for attempt in range(3):  # 氣象署偶爾中途斷線，重試
        try:
            r = requests.get(API + ds, params={"Authorization": key, "format": "JSON", **params},
                             headers={"User-Agent": UA}, timeout=120)
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))
    js = r.json()
    if js.get("success") != "true":
        raise RuntimeError(f"{ds} 回傳失敗")
    (RAW / "cwa").mkdir(parents=True, exist_ok=True)
    (RAW / "cwa" / f"{ds}.json").write_text(r.text, encoding="utf-8")
    return js


def elements(js):
    loc = js["records"]["Locations"][0]["Location"][0]
    return {e["ElementName"]: e["Time"] for e in loc["WeatherElement"]}


def ts(s):
    return datetime.fromisoformat(s.replace(" ", "T")).replace(tzinfo=None)


def val(entry):
    return entry["ElementValue"][0]


def num(x):
    """數字欄位；氣象署風級在微風時寫成「<= 1」，取其中的數字。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        import re
        m = re.search(r"\d+(\.\d+)?", str(x or ""))
        return float(m.group()) if m else None


def judge_spray(s, nxt):
    why, lvl = [], "ok"
    bf, pop, npop = s["bf"], s["pop"], (nxt or {}).get("pop")
    rainy = any(w in s["wx"] for w in RAIN_WORDS)
    if bf is not None and bf >= 4:
        lvl = "no"; why.append(f"風 {int(bf)} 級")
    elif bf is not None and bf >= 3:
        lvl = "warn"; why.append(f"風 {int(bf)} 級")
    if rainy:
        lvl = "no"; why.append(s["wx"])
    if pop is not None:
        if pop > 40:
            lvl = "no"; why.append(f"降雨機率 {int(pop)}%")
        elif pop > 20:
            lvl = max(lvl, "warn", key=["ok", "warn", "no"].index); why.append(f"降雨機率 {int(pop)}%")
    if npop is not None:
        if npop > 50:
            lvl = "no"; why.append(f"下一時段降雨機率 {int(npop)}%")
        elif npop > 30:
            lvl = max(lvl, "warn", key=["ok", "warn", "no"].index); why.append(f"下一時段降雨機率 {int(npop)}%")
    if s["tmax"] is not None and s["tmax"] >= 32:
        lvl = max(lvl, "warn", key=["ok", "warn", "no"].index); why.append(f"高溫 {int(s['tmax'])}°C")
    if s["heatWarn"] == "危險":
        lvl = "no"; why.append("熱傷害危險")
    return {"lvl": lvl, "why": why}


def judge_dry(s):
    h = ts(s["start"]).hour
    if h < 6 or h >= 18:
        return {"lvl": "night", "why": ["夜間"]}
    why = []
    rainy = any(w in s["wx"] for w in RAIN_WORDS)
    pop = s["pop"] or 0
    if rainy or pop > 20:
        why.append(s["wx"] if rainy else f"降雨機率 {int(pop)}%")
        return {"lvl": "no", "why": why}
    sunny = ("晴" in s["wx"] or "多雲" in s["wx"])
    if pop <= 10 and sunny and (s["rh"] is None or s["rh"] <= 75):
        return {"lvl": "ok", "why": [s["wx"]]}
    if not sunny:
        why.append(s["wx"])
    if pop > 10:
        why.append(f"降雨機率 {int(pop)}%")
    if s["rh"] is not None and s["rh"] > 75:
        why.append(f"濕度 {int(s['rh'])}%")
    return {"lvl": "warn", "why": why}


def build(key):
    e3 = elements(get("F-D0047-041", key, LocationName=TOWN))
    hourly = {}
    for name, field in [("溫度", "Temperature"), ("相對濕度", "RelativeHumidity"), ("體感溫度", "ApparentTemperature")]:
        for t in e3.get(name, []):
            hourly.setdefault(ts(t["DataTime"]), {})[field] = num(val(t)[field])
    wind = {ts(t["DataTime"]): val(t) for t in e3.get("風速", [])}
    wdir = {ts(t["DataTime"]): val(t)["WindDirection"] for t in e3.get("風向", [])}
    wx = {ts(t["StartTime"]): val(t)["Weather"] for t in e3.get("天氣現象", [])}
    desc = {ts(t["StartTime"]): val(t)["WeatherDescription"] for t in e3.get("天氣預報綜合描述", [])}

    # 熱傷害（全台檔，取瑞穗鄉）
    heat = {}
    try:
        h = get("M-A0085-001", key)
        for c in h["records"]["Locations"]:
            for l in c["Location"]:
                if c["CountyName"] == "花蓮縣" and l["TownName"] == TOWN:
                    for t in l["Time"]:
                        heat[ts(t["IssueTime"])] = t["WeatherElements"]
    except Exception as ex:
        print("熱傷害資料下載失敗：", type(ex).__name__)

    slots = []
    for t in e3.get("3小時降雨機率", []):
        st, en = ts(t["StartTime"]), ts(t["EndTime"])
        hrs = [v for k, v in hourly.items() if st <= k < en]
        temps = [v["Temperature"] for v in hrs if v.get("Temperature") is not None]
        rhs = [v["RelativeHumidity"] for v in hrs if v.get("RelativeHumidity") is not None]
        feels = [v["ApparentTemperature"] for v in hrs if v.get("ApparentTemperature") is not None]
        # 風速時間點不一定對齊時段開頭：取時段內最大者，沒有就取最接近的時間點
        inslot = [v for k, v in wind.items() if st <= k < en]
        if inslot:
            w = max(inslot, key=lambda v: num(v.get("BeaufortScale")) or 0)
        elif wind:
            w = wind[min(wind, key=lambda k: abs((k - st).total_seconds()))]
        else:
            w = {}
        hv = heat.get(st, {})
        slots.append({
            "start": st.isoformat(timespec="minutes"), "end": en.isoformat(timespec="minutes"),
            "pop": num(val(t)["ProbabilityOfPrecipitation"]), "wx": wx.get(st, ""), "desc": desc.get(st, ""),
            "ws": num(w.get("WindSpeed")), "bf": num(w.get("BeaufortScale")), "wd": wdir.get(st, ""),
            "tmax": max(temps) if temps else None, "tmin": min(temps) if temps else None,
            "rh": round(sum(rhs) / len(rhs)) if rhs else None, "feel": max(feels) if feels else None,
            "heat": hv.get("HeatInjuryIndex"), "heatWarn": hv.get("HeatInjuryWarning", ""),
        })
    for i, s in enumerate(slots):
        s["spray"] = judge_spray(s, slots[i + 1] if i + 1 < len(slots) else None)
        s["dry"] = judge_dry(s)

    # 一週（逐 12 小時）
    week = []
    try:
        e7 = elements(get("F-D0047-043", key, LocationName=TOWN))
        idx = {}
        for name, field in [("12小時降雨機率", "ProbabilityOfPrecipitation"), ("天氣現象", "Weather"),
                            ("最高溫度", "MaxTemperature"), ("最低溫度", "MinTemperature"),
                            ("平均相對濕度", "RelativeHumidity"), ("紫外線指數", "UVIndex")]:
            for t in e7.get(name, []):
                idx.setdefault(t["StartTime"], {"start": ts(t["StartTime"]).isoformat(timespec="minutes"),
                                                "end": ts(t["EndTime"]).isoformat(timespec="minutes")})[field] = val(t)[field]
        week = [idx[k] for k in sorted(idx)]
    except Exception as ex:
        print("一週預報下載失敗：", type(ex).__name__)

    return {"town": TOWN, "updated": datetime.now().isoformat(timespec="minutes"), "slots": slots, "week": week,
            "heatSlots": [{"t": k.isoformat(timespec="minutes"), **v} for k, v in sorted(heat.items())]}


def main():
    key = load_key("CWA_API_KEY")
    if not key:
        print("找不到 CWA_API_KEY（環境變數或 ~/.ruisui_keys.env）")
        return 1
    try:
        data = build(key)
    except Exception as ex:  # 保留舊檔
        print("氣象署下載失敗，沿用舊資料：", type(ex).__name__, str(ex)[:100].replace(key, "***"))
        return 1
    (WEB_DATA / "weather.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    from collections import Counter
    print("時段數：", len(data["slots"]), "一週：", len(data["week"]), "熱傷害：", len(data["heatSlots"]))
    print("噴藥：", dict(Counter(s["spray"]["lvl"] for s in data["slots"])),
          "曬穀：", dict(Counter(s["dry"]["lvl"] for s in data["slots"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
