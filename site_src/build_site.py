"""把各張地圖的成品組成一個網站，輸出到 site/（GitHub Pages 發布這個資料夾）。

用法：python -I site_src/build_site.py

- 只「複製」各地圖的成品檔，不修改原始檔。
- 每頁加一顆「回首頁」小按鈕；#10 地形頁的「網址待補」卡片換成真正的連結（只改 site/ 裡的副本）。
- 首頁卡片依 PAGES 清單產生；某頁檔案不存在就略過並印出警告。
"""
import datetime as dt
import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site_src"
OUT = ROOT / "site"

# slug：網址路徑；src：成品所在資料夾；files：要複製的檔案（glob）；
# entry：頁面檔名；update：給居民看的更新頻率
PAGES = [
    dict(slug="care", src="web", entry="index.html", files=["*.html", "data/*.json"],
         title="就醫地圖", icon="🏥", desc="最近的診所、急診、AED、藥局要開車幾分鐘；今天哪些院所有看診；坐火車、客運去看病", update="每天更新"),
    dict(slug="care", src="web", entry="safe.html", files=[],
         title="避難路線", icon="🧭", desc="從你家到最近 3 處避難所的建議路線，盡量避開土石流範圍與封閉道路", update="警戒約每 3 小時更新"),
    dict(slug="care", src="web", entry="farm.html", files=[],
         title="農事天氣窗", icon="🌦️", desc="未來一週哪些時段適合噴藥、曬穀，熱傷害警示", update="每 6 小時更新"),
    dict(slug="school", src="08_通學距離", entry="index.html", files=["index.html"],
         title="通學距離", icon="🎒", desc="各村到國中小、高中職的距離和開車、步行時間，學生數與學區", update="每學年更新"),
    dict(slug="rice", src="09_稻作衛星", entry="index.html",
         files=["index.html", "data/data.js", "timelapse.gif", "overlays/*.png"],
         title="稻作衛星", icon="🌾", desc="衛星影像看各村田區的綠度變化與縮時動畫", update="約每 5 天一張影像",
         hidden=False),  # 2026-11-06 曾因 NDVI 全算成 1.0 下架；修正重算後（森林約 0.85、市區約 0.56）重新上架
    dict(slug="terrain", src="10_地形", entry="index.html", files=["index.html", "assets/*", "lib/*"],
         title="地形故事", icon="⛰️", desc="3D 地形、秀姑巒溪、北回歸線、溫泉的位置", update="不需更新"),
]

BACK_BTN = """
<a href="../" class="rs-home" aria-label="回瑞穗生活地圖首頁"
 style="position:fixed;left:10px;bottom:calc(10px + env(safe-area-inset-bottom));z-index:99999;
 background:rgba(38,48,43,.86);color:#fff;font:600 14px/1 system-ui,'Microsoft JhengHei',sans-serif;
 padding:9px 13px;border-radius:999px;text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.25)">← 瑞穗生活地圖</a>
"""


def copy_page(p):
    src = ROOT / p["src"]
    dst = OUT / p["slug"]
    n = 0
    for pat in p["files"]:
        hits = sorted(src.glob(pat))
        if not hits:
            print(f"  警告：{p['src']}/{pat} 找不到檔案")
        for f in hits:
            if f.is_file():
                t = dst / f.relative_to(src)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)
                n += 1
    return n


def patch_html(path, slug):
    s = path.read_text(encoding="utf-8")
    if "rs-home" not in s:
        s = s.replace("</body>", BACK_BTN + "</body>", 1) if "</body>" in s else s + BACK_BTN
    if slug == "terrain":  # 把 #10 的佔位卡換成連結
        links = {"#7 生活可近性": ("../care/", "就醫地圖：診所、急診、AED、交通"),
                 "#8 通學距離": ("../school/", "各村到學校的距離與時間"),
                 "#9 稻作衛星": ("../rice/", "衛星看田區綠度變化")}
        live = {p["slug"] for p in PAGES if not p.get("hidden")}
        for k, (href, d) in links.items():
            if href.strip("./") not in live:
                continue  # 未發布的頁面維持「網址待補」
            s = re.sub(rf'<div class="card ph"><b>{re.escape(k)}</b>網址待補</div>',
                       f'<a class="card" href="{href}" style="text-decoration:none;color:inherit"><b>{k}</b>{d}</a>', s)
    path.write_text(s, encoding="utf-8")


def build_home(pages):
    cards = []
    for p in pages:
        href = f"{p['slug']}/" if p["entry"] == "index.html" else f"{p['slug']}/{p['entry']}"
        cards.append(
            f'<a class="link" href="{href}"><span class="ic" aria-hidden="true">{p["icon"]}</span>'
            f'<span class="tx"><b>{html.escape(p["title"])}</b><small>{html.escape(p["desc"])}</small>'
            f'<em>{html.escape(p["update"])}</em></span><span class="go" aria-hidden="true">›</span></a>')
    tpl = (SRC / "home.html").read_text(encoding="utf-8")
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    return tpl.replace("{{CARDS}}", "\n".join(cards)).replace("{{BUILT}}", now)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)  # site/ 完全由本程式產生，可以整個重建
    OUT.mkdir()
    built = []
    for p in PAGES:
        if p.get("hidden"):
            print(f"暫不發布 {p['title']}（hidden=True）")
            continue
        entry = ROOT / p["src"] / p["entry"]
        if not entry.exists():
            print(f"略過 {p['title']}：找不到 {p['src']}/{p['entry']}")
            continue
        n = copy_page(p) if p["files"] else 0
        built.append(p)
        print(f"OK {p['title']:6s} → site/{p['slug']}/  複製 {n} 個檔案")
    for f in OUT.glob("*/*.html"):
        patch_html(f, f.parent.name)
    (OUT / "index.html").write_text(build_home(built), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copy2(SRC / "favicon.svg", OUT / "favicon.svg")
    (OUT / "build.json").write_text(json.dumps(
        {"built": dt.datetime.now().isoformat(timespec="seconds"), "pages": [p["title"] for p in built]},
        ensure_ascii=False), encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"完成：site/ 共 {size/1e6:.1f} MB")


if __name__ == "__main__":
    main()
