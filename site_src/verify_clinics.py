"""核對就醫地圖上的院所資料是否與健保署原始檔一致。

用法：python -I site_src/verify_clinics.py  → 結果寫到 data/verify_clinics.md（不上傳）

比對 web/data/facilities.json（網頁用）與 data/raw/ 的健保署檔案：
名稱、地址、電話、科別、固定看診時段、開業狀況、合約到期日；並列出只定位到路段的院所。
"""
import csv
import datetime as dt
import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SLOT = ["上午", "下午", "晚上"]
DAY = "一二三四五六日"


def norm(x):
    return unicodedata.normalize("NFKC", x or "").replace(" ", "")


def rows(name):
    p = RAW / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def hours_text(bits):
    if not bits or len(bits) != 21:
        return "（無資料）"
    out = []
    for d in range(7):
        open_ = [SLOT[s] for s in range(3) if bits[s * 7 + d] == "N"]  # N＝有看診
        out.append(f"{DAY[d]}:{'/'.join(open_) if open_ else '休'}")
    return " ".join(out)


def main():
    fac = json.loads((ROOT / "web" / "data" / "facilities.json").read_text(encoding="utf-8"))
    web = {f["id"]: f for f in fac["facilities"] if f.get("cat") in ("clinic", "dental", "tcm", "pharmacy", "hospital")}
    inst = {}
    for n in ("nhi_clinic.csv", "nhi_pharmacy.csv", "nhi_hosp_district.csv", "nhi_hosp_regional.csv", "nhi_hosp_center.csv"):
        for r in rows(n):
            inst[r["醫事機構代碼"]] = r
    hours = {r["醫事機構代碼"]: r for r in rows("nhi_hours.csv")}
    today = dt.date.today().strftime("%Y%m%d")

    lines = [f"# 院所資料核對（{dt.datetime.now():%Y-%m-%d %H:%M}）",
             f"網頁資料：facilities.json built {fac['meta'].get('built')}、看診時段 {fac['meta'].get('hours_updated')}",
             ""]
    problems = []
    ruisui = []
    for fid, f in sorted(web.items(), key=lambda x: (x[1].get("town") != "瑞穗鄉", x[1].get("town", ""), x[1]["cat"])):
        r, h = inst.get(fid), hours.get(fid)
        issues = []
        if not r:
            issues.append("健保署院所名單查無此代碼（可能已終止合約）")
        else:
            if r["醫事機構名稱"] != f["name"] and r["醫事機構名稱"] not in f["name"] and f["name"] not in r["醫事機構名稱"]:
                issues.append(f"名稱不同：健保署「{r['醫事機構名稱']}」")
            if norm(r.get("地址")) != norm(f.get("addr")):
                issues.append(f"地址不同：健保署「{r.get('地址')}」")
            if norm(r.get("電話")) != norm(f.get("tel")):
                issues.append(f"電話不同：健保署「{r.get('電話')}」")
            end = (r.get("終止合約或歇業日期") or "").strip()
            if end and end.isdigit() and len(end) == 8 and end < today:
                issues.append(f"合約／歇業日期 {end} 已過")
        if h:
            if h.get("開業狀況") not in ("0", ""):
                issues.append(f"開業狀況代碼 {h.get('開業狀況')}（0 才是開業）")
            if h.get("看診星期") != f.get("hours"):
                issues.append("看診時段與健保署不同")
        elif f["cat"] != "hospital":
            issues.append("健保署看診時段檔查無資料")
        if f.get("precision") != "exact":
            issues.append(f"位置只到{f.get('geo') or f.get('precision')}（地圖點位可能偏差，導航請用地址）")
        rec = (f, r, h, issues)
        if f.get("town") == "瑞穗鄉":
            ruisui.append(rec)
        if [i for i in issues if not i.startswith("位置只到")]:
            problems.append(rec)

    lines.append(f"## 瑞穗鄉院所（{len(ruisui)} 家）")
    lines.append("")
    for f, r, h, issues in ruisui:
        lines.append(f"### {f['name']}（{f['cat']}）")
        lines.append(f"- 地址：{f.get('addr')}　電話：{f.get('tel')}")
        lines.append(f"- 科別：{f.get('dept') or '（未列）'}")
        lines.append(f"- 固定看診：{hours_text(f.get('hours'))}")
        if f.get("note"):
            lines.append(f"- 看診備註：{f['note']}")
        if r:
            lines.append(f"- 健保合約到：{r.get('終止合約或歇業日期') or '（未列）'}")
        lines.append(f"- 核對：{'；'.join(issues) if issues else '與健保署一致'}")
        lines.append("")
    lines.append(f"## 全部 {len(web)} 家中，資料不一致的（不含只是定位精度）：{len(problems)} 家")
    for f, r, h, issues in problems:
        lines.append(f"- {f.get('town')} {f['name']}：{'；'.join(i for i in issues if not i.startswith('位置只到'))}")
    out = ROOT / "data" / "verify_clinics.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
