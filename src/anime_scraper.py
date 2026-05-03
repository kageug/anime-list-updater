"""アニメイトタイムズのシーズンタグページからアニメ一覧＋OP/EDを抽出する。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import requests
from bs4 import BeautifulSoup, Tag

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BASE_URL = "https://www.animatetimes.com"


@dataclass
class Song:
    kind: str          # "OP" | "ED" | "主題歌"
    index: int         # 1, 2, ... (複数曲時)
    title: str
    artist: str

    def key(self) -> str:
        return f"{self.kind}{self.index}|{self.title}|{self.artist}"


@dataclass
class Anime:
    season_id: int
    season_label: str
    order: int
    title: str
    detail_url: str
    is_rerun: bool
    broadcast_start: str = ""
    broadcast_format: str = ""
    animation_studio: str = ""
    songs: list[Song] = field(default_factory=list)


_SONG_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<kind>OP|ED|主題歌)(?P<index>\d*)\s*[：:]\s*)?  # 任意の "OP1：" 等
    [「『](?P<title>[^」』]+)[」』]                        # 曲名（「」 or 『』）
    \s*(?P<artist>.*?)\s*$                                # 残りはアーティスト
    """,
    re.VERBOSE,
)


def fetch_tag_page(season_id: int, *, timeout: int = 30) -> str:
    """指定シーズンタグページのHTMLを取得。"""
    url = f"{BASE_URL}/tag/details.php?id={season_id}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_season_label(html: str) -> str:
    """ページタイトルから「2026春アニメ」等のラベルを抽出。"""
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("title")
    if not title_tag:
        return ""
    text = title_tag.get_text()
    m = re.search(r"(\d{4}[春夏秋冬])アニメ", text)
    return m.group(1) + "アニメ" if m else ""


def _clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _parse_songs(cell_html: str) -> list[Song]:
    """主題歌セルHTMLから曲リストを抽出。<br>区切りで複数行対応。"""
    # <br> を改行に正規化（タグ前後の隙間も許容）
    normalized = re.sub(r"<\s*br\s*/?\s*>", "\n", cell_html, flags=re.IGNORECASE)
    # タグを除去（区切り文字なし＝<a>タグで分割されたテキストを連結）
    text = BeautifulSoup(normalized, "lxml").get_text("")

    songs: list[Song] = []
    op_count = 0
    ed_count = 0
    other_count = 0
    for raw_line in text.splitlines():
        line = _clean_text(raw_line)
        if not line:
            continue
        m = _SONG_LINE_RE.match(line)
        if not m:
            continue
        kind = m.group("kind") or "主題歌"
        title = _clean_text(m.group("title"))
        artist = _clean_text(m.group("artist"))
        # 後ろの余分な記号削除
        artist = re.sub(r"[（(]\s*[)）]\s*$", "", artist).strip()

        if kind == "OP":
            op_count += 1
            idx = op_count
        elif kind == "ED":
            ed_count += 1
            idx = ed_count
        else:
            other_count += 1
            idx = other_count

        songs.append(Song(kind=kind, index=idx, title=title, artist=artist))
    return songs


def _table_rows(table: Tag) -> dict[str, Tag]:
    """tableから {ラベル: <th>セル} の辞書を作る。"""
    rows: dict[str, Tag] = {}
    for tr in table.find_all("tr"):
        td = tr.find("td")
        th = tr.find("th")
        if td and th:
            label = _clean_text(td.get_text())
            rows[label] = th
    return rows


def _extract_anime_block(h2: Tag, season_id: int, season_label: str) -> Anime | None:
    """1つのアニメ <h2> 〜 次の <h2> 直前までを解析する。"""
    a = h2.find("a")
    if not a:
        return None
    title_raw = _clean_text(a.get_text())
    is_rerun = "再放送" in title_raw
    title = re.sub(r"[（(]再放送[)）]\s*$", "", title_raw).strip()
    detail_url = a.get("href", "")

    try:
        order = int(h2.get("id", "0"))
    except ValueError:
        order = 0

    anime = Anime(
        season_id=season_id,
        season_label=season_label,
        order=order,
        title=title,
        detail_url=detail_url,
        is_rerun=is_rerun,
    )

    # h2 直後のテーブル（最初に出現するもの）を取得
    table = None
    for sib in h2.next_siblings:
        if isinstance(sib, Tag):
            if sib.name == "h2":
                break
            t = sib.find("table") if sib.name != "table" else sib
            if t:
                table = t
                break
    if table is None:
        return anime

    rows = _table_rows(table)

    if "スケジュール" in rows:
        anime.broadcast_start = _clean_text(rows["スケジュール"].get_text(" "))
    if "放送形態" in rows:
        anime.broadcast_format = _clean_text(rows["放送形態"].get_text(" "))
    if "スタッフ" in rows:
        staff_text = rows["スタッフ"].get_text("\n")
        m = re.search(r"アニメーション制作[：:]\s*([^\n]+)", staff_text)
        if m:
            anime.animation_studio = _clean_text(m.group(1))

    if "主題歌" in rows:
        anime.songs = _parse_songs(rows["主題歌"].decode_contents())

    return anime


def parse_anime_list(html: str, season_id: int) -> tuple[str, list[Anime]]:
    """シーズンタグページHTMLからアニメ一覧を抽出。(season_label, anime_list)。"""
    season_label = parse_season_label(html)
    soup = BeautifulSoup(html, "lxml")
    animes: list[Anime] = []
    for h2 in soup.find_all("h2", class_="c-heading-h2"):
        if not h2.get("id"):
            continue  # "目次" 見出しなど id なしはスキップ
        anime = _extract_anime_block(h2, season_id, season_label)
        if anime:
            animes.append(anime)
    return season_label, animes


def scrape_season(season_id: int) -> tuple[str, list[Anime]]:
    """シーズンID指定で一括取得。"""
    html = fetch_tag_page(season_id)
    return parse_anime_list(html, season_id)


if __name__ == "__main__":
    import sys

    sid = int(sys.argv[1]) if len(sys.argv) > 1 else 5228
    label, anis = scrape_season(sid)
    print(f"season: {label}  ({len(anis)} anime)")
    for a in anis[:5]:
        print(f"\n[{a.order}] {a.title}  rerun={a.is_rerun}")
        print(f"  studio: {a.animation_studio}")
        print(f"  schedule: {a.broadcast_start}")
        for s in a.songs:
            print(f"  {s.kind}{s.index}: {s.title} / {s.artist}")
