"""瑞穗生活地圖：共用路徑與設定。"""
import os
from pathlib import Path
from datetime import date

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "data" / "raw"
DATA = BASE / "data"
WEB = BASE / "web"
WEB_DATA = WEB / "data"
for p in (RAW, DATA, WEB_DATA):
    p.mkdir(parents=True, exist_ok=True)

UA = "ruisui-life-map/0.1 (community open-data map)"
TODAY = date.today()

# 地圖範圍：瑞穗鄉與鄰近鄉鎮（光復、玉里、鳳林）的院所都要算進來
NEAR_TOWNS = ["瑞穗鄉", "光復鄉", "玉里鎮", "鳳林鎮", "萬榮鄉", "卓溪鄉"]
# 設施清單範圍（南, 西, 北, 東）：南到玉里、北到花蓮市（大醫院、車站）
FAC_BBOX = (23.28, 121.25, 24.02, 121.63)
# 開車路網範圍：玉里到南平（瑞穗居民最近的急診在鳳林、玉里），面積小於 Overpass 單次查詢上限
NET_BBOX = (23.28, 121.25, 23.80, 121.52)

OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def load_key(name):
    """讀金鑰：先看環境變數，再看家目錄 .ruisui_keys.env。絕不印出內容。"""
    v = os.environ.get(name)
    if v:
        return v.strip()
    f = Path.home() / ".ruisui_keys.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return None
