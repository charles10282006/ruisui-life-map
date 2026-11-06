# 瑞穗生活服務網頁

| 頁面 | 檔案 | 更新 |
|---|---|---|
| 瑞穗就醫地圖（#7 生活可近性＋就醫交通） | `web/index.html` | `update_daily.bat` 每天 |
| 瑞穗農事天氣窗（B：噴藥、曬穀、熱傷害） | `web/farm.html` | `update_weather.bat` 每 6 小時 |
| 瑞穗避難路線（C：避開土石流範圍與封閉道路） | `web/safe.html` | `update_alerts.bat` 平時每 3 小時、颱風豪雨每 30 分鐘；`build_hazard.py` 每年 |

## C 防災安全路線

- `scripts/build_hazard.py` → `web/data/hazard.json`：路網（沿用 graph_drive.pkl）、土石流影響範圍（115 年度）、避難所（縣府版為主、消防署版補充）
- `scripts/fetch_alerts.py` → `web/data/alerts.json`：土石流／大規模崩塌警戒（免金鑰）、TDX 公路事件、氣象署天氣特報
- 路線在使用者手機上計算（Dijkstra）：經過土石流範圍的路段成本 ×5，黃色警戒 ×10，紅色警戒不走；公路局「阻斷、坍方、雙向封閉」事件 60 公尺內的路段不走，單一車道封閉、調撥只標「注意」
- 避難所座標校正：`data/shelter_overrides.csv`（名稱,lat,lon,備註）
- 個資：不輸出管理人、聯絡人姓名、手機、Email
- 未納入：淹水潛勢（找不到花蓮縣公開下載檔）、鄉道與產業道路封閉（無開放資料）
- 已知資料問題：鶴岡國小三個來源座標不一致（縣府版正確，與同路 168 號鶴岡遊客中心相鄰）；消防署版與衛福部 AED 名冊的鶴岡國小座標偏差 2–3.6 公里

## B 農事天氣窗

- 程式：`scripts/fetch_weather.py` → `web/data/weather.json`（需要 `CWA_API_KEY`）
- 來源：氣象署 F-D0047-041（瑞穗鄉 3 天）、F-D0047-043（1 週）、M-A0085-001（熱傷害）
- 判斷門檻寫在 `fetch_weather.py` 開頭說明：噴藥風速參考農業藥物試驗所無人機施藥指引（蒲福 2 級），降雨、溫度、曬穀門檻為自訂參考值；熱傷害直接用氣象署警示。
- 預報是瑞穗鄉代表點，山區、河谷可能不同。

---

# 瑞穗就醫地圖（#7 生活可近性＋就醫交通）

給瑞穗鄉居民用的手機網頁：點自己家的位置，就能看到最近的診所、急診、AED、藥局要開車幾分鐘，現在哪些院所有看診，以及坐火車、客運去看病的班次。

## 資料夾

```
瑞穗生活地圖/
├─ scripts/
│  ├─ common.py            路徑、範圍、金鑰讀取（金鑰不寫進程式）
│  ├─ fetch_daily.py       每日下載：健保院所、看診時段、AED、公路客運
│  ├─ build_facilities.py  整理設施＋定位 → web/data/facilities.json
│  ├─ fetch_transit.py     台鐵（TDX）＋客運時刻 → web/data/transit.json
│  └─ build_access.py      路網計算（每年一次）→ access.json / roads.json / villages.json
├─ data/
│  ├─ raw/                 下載的原始檔
│  ├─ geocode_cache.csv    路段定位快取
│  ├─ geocode_overrides.csv  手動校正座標（地址,lat,lon,備註）← 發現位置不準就填這裡
│  └─ update.log           每日更新紀錄
├─ web/                    要上傳的網站（index.html + data/）
└─ update_daily.bat        每日更新批次檔
```

## 更新頻率

| 內容 | 程式 | 頻率 |
|---|---|---|
| 看診時段、院所名單、AED、客運、台鐵 7 天時刻 | `update_daily.bat` | 每天 |
| 路網與開車時間 | `python scripts/build_access.py` | 每年一次，或院所搬家／新開時 |

重跑路網前刪掉 `data/graph_drive.pkl`、`data/graph_walk.pkl` 才會重新下載道路。

## 金鑰

放在 `%USERPROFILE%\.ruisui_keys.env`（專案資料夾外，不會上傳）；GitHub Actions 改用 repo 的 Secrets（`CWA_API_KEY`、`TDX_CLIENT_ID`、`TDX_CLIENT_SECRET`）：

```
CWA_API_KEY=...
TDX_CLIENT_ID=...
TDX_CLIENT_SECRET=...
```

## 已知限制

- 開車時間依道路等級假設車速，不含紅綠燈、塞車、停車；起點是最近的道路點，不是門口。
- 部分院所只定位到路段（網頁標「約略位置」），可在 `geocode_overrides.csv` 手動校正。
- 看診時段是健保署登錄的固定時段，不含臨時休診。
- 客運各站時間是公路局登錄的推估值。
- 各村統計以道路點計，不是人口加權（之後可接 SEGIS 最小統計區人口）。
