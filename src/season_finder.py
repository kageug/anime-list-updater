"""
任意のアニメイトタイムズ シーズンタグページのナビメニューから
春/夏/秋/冬 4季のタグIDを発見し、現行 + 次期シーズンを返す。

ナビ例:
  <a name="menu_burger_spring"  href="/tag/details.php?id=5228">春アニメ</a>
  <a name="menu_burger_summer"  href="/tag/details.php?id=5806">夏アニメ</a>
  <a name="menu_burger_autumn"  href="/tag/details.php?id=5947">秋アニメ</a>
  <a name="menu_burger_winter"  href="/tag/details.php?id=6212">冬アニメ</a>
"""

from __future__ import annotations

import datetime as dt
import re

import requests

from .anime_scraper import BASE_URL, USER_AGENT

# ナビ取得の起点（どのシーズンタグでも全季のリンクが入っている）
SEED_URL = f"{BASE_URL}/tag/details.php?id=5228"

SEASON_KEYS = ("spring", "summer", "autumn", "winter")
JP_LABEL = {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"}


def fetch_seed_html(*, timeout: int = 30) -> str:
    resp = requests.get(SEED_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def discover_season_ids(html: str | None = None) -> dict[str, int]:
    """ナビメニューから {season_key: tag_id} を返す。"""
    if html is None:
        html = fetch_seed_html()
    pattern = re.compile(
        r'name="menu_burger_(spring|summer|autumn|winter)"[^>]*href="/tag/details\.php\?id=(\d+)"',
    )
    found: dict[str, int] = {}
    for m in pattern.finditer(html):
        key, tag_id = m.group(1), int(m.group(2))
        found.setdefault(key, tag_id)
    # 古いHTMLでは順序が入れ替わるので逆方向もサポート
    pattern2 = re.compile(
        r'href="/tag/details\.php\?id=(\d+)"[^>]*name="menu_burger_(spring|summer|autumn|winter)"',
    )
    for m in pattern2.finditer(html):
        tag_id, key = int(m.group(1)), m.group(2)
        found.setdefault(key, tag_id)
    return found


def current_season_key(today: dt.date | None = None) -> str:
    """現在の季節を返す（春=3-5月, 夏=6-8月, 秋=9-11月, 冬=12-2月）。"""
    today = today or dt.date.today()
    m = today.month
    if 3 <= m <= 5:
        return "spring"
    if 6 <= m <= 8:
        return "summer"
    if 9 <= m <= 11:
        return "autumn"
    return "winter"


def next_season_key(current: str | None = None) -> str:
    cur = current or current_season_key()
    idx = SEASON_KEYS.index(cur)
    return SEASON_KEYS[(idx + 1) % 4]


def resolve_seasons(spec: str = "current,next") -> list[tuple[str, int]]:
    """
    spec: "current", "next", "all", "current,next" 等のカンマ区切り。
    返値: [(season_key, tag_id), ...]
    """
    ids = discover_season_ids()
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for token in spec.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token == "current":
            keys = [current_season_key()]
        elif token == "next":
            keys = [next_season_key()]
        elif token == "all":
            keys = list(SEASON_KEYS)
        elif token in SEASON_KEYS:
            keys = [token]
        else:
            print(f"[warn] unknown season spec: {token}")
            continue
        for k in keys:
            if k in seen:
                continue
            tid = ids.get(k)
            if tid is None:
                print(f"[warn] tag id not found for {k}")
                continue
            out.append((k, tid))
            seen.add(k)
    return out


if __name__ == "__main__":
    ids = discover_season_ids()
    print("All season tag IDs:")
    for k in SEASON_KEYS:
        print(f"  {k} ({JP_LABEL[k]}): {ids.get(k, 'N/A')}")
    print(f"\nToday's current season: {current_season_key()}")
    print(f"Next season: {next_season_key()}")
    print("\nResolved (current,next):")
    for key, tid in resolve_seasons("current,next"):
        print(f"  {key}: id={tid}")
