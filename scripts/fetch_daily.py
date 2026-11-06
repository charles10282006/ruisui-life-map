"""每日下載：健保院所、看診時段、AED、公路客運時刻。

用法：python scripts/fetch_daily.py
所有來源皆為政府資料開放授權條款第1版，免金鑰。
"""
import sys
import requests
from common import RAW, UA

SOURCES = {
    # 健保署（data.gov.tw 39291 / 39283 / 39284 / 39280-39282）
    "nhi_hours.csv": "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21006-001",
    "nhi_clinic.csv": "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21004-009",
    "nhi_pharmacy.csv": "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21005-001",
    "nhi_hosp_district.csv": "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21003-003",
    "nhi_hosp_regional.csv": "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21002-005",
    "nhi_hosp_center.csv": "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21001-003",
    # 衛福部 全國AED（每日更新）
    "aed.csv": "https://tw-aed.mohw.gov.tw/openData?t=csv",
    # 公路局 客運時刻（data.gov.tw 6052）
    "bus/timetable.csv": "https://www.taiwanbus.tw/TimeTableExport/timetable.csv",
}

MIN_BYTES = 10_000  # 小於這個大小視為下載失敗，保留舊檔


def main():
    ok = True
    for name, url in SOURCES.items():
        dest = RAW / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=300)
            r.raise_for_status()
            if len(r.content) < MIN_BYTES:
                raise ValueError(f"內容太小（{len(r.content)} bytes）")
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
            print(f"OK   {name}  {len(r.content):,} bytes")
        except Exception as e:  # 保留舊檔，繼續下一個
            ok = False
            print(f"FAIL {name}: {e}（沿用舊檔）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
