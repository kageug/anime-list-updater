"""yt-dlp で OP/ED の YouTube 動画を検索し、公式チャンネルから採用する。"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import time
import unicodedata
from pathlib import Path

from .anime_scraper import Anime

CACHE_FILENAME = "youtube_cache.json"

# キャッシュ再検索ポリシー
FOUND_TTL_DAYS = 7      # ヒット済みは7日キャッシュ
NOT_FOUND_TTL_HOURS = 22  # 未ヒットは22時間で期限切れ（毎日リトライ）

SEARCH_RESULTS = 8       # ytsearch で取得する候補数
THROTTLE_SEC = 1.5       # リクエスト間隔
RETRY_MAX = 3
TIMEOUT_SEC = 60

# 公式判定で除外するアーティスト名内の連結語
ARTIST_FILLER = {"feat", "feat.", "ft", "ft.", "with", "and", "&", "from", "vs", "x"}

# 検索アーティスト名を綺麗にする補助
_BRACKETS_RE = re.compile(r"[\(\[（［]([^\)\]）］]*)[\)\]）］]")


def _normalize(s: str) -> str:
    """大小文字差・全角半角差・空白・記号を吸収して比較用に正規化。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    # 記号・空白除去
    s = re.sub(r"[\s・/／\-－—_「」『』【】〈〉<>!！?？.,、。：:；;~〜♪…]+", "", s)
    return s


def _artist_tokens(artist: str) -> list[str]:
    """アーティスト名から照合用トークンを取り出す。"""
    if not artist:
        return []
    cleaned = _BRACKETS_RE.sub(" ", artist)  # (CV.~) など括弧内除去
    raw = re.split(r"[\s,、，&×\+]+", cleaned)
    tokens: list[str] = []
    for t in raw:
        if not t:
            continue
        if t.lower().strip(".") in ARTIST_FILLER:
            continue
        n = _normalize(t)
        if len(n) >= 2:
            tokens.append(n)
    return tokens


def _anime_tokens(anime_title: str) -> list[str]:
    """アニメタイトルから照合用トークンを取り出す。"""
    if not anime_title:
        return []
    cleaned = re.sub(r"第\s*\d+\s*[期季クール]|シーズン\s*\d+|Season\s*\d+", " ", anime_title, flags=re.IGNORECASE)
    cleaned = re.sub(r"\d+(st|nd|rd|th)?$", " ", cleaned)
    raw = re.split(r"[\s・／/!！?？]+", cleaned)
    tokens: list[str] = []
    for t in raw:
        n = _normalize(t)
        if len(n) >= 3:
            tokens.append(n)
    if not tokens:  # 短いタイトルはまるごと
        n = _normalize(anime_title)
        if n:
            tokens.append(n)
    return tokens


# アニメ配給／チャンネル系の語（含まれていれば「公式系チャンネル」と緩く判定）
_OFFICIAL_CHANNEL_HINTS = (
    "アニメ", "anime", "公式", "official", "channel", "チャンネル",
    "kadokawa", "toho", "aniplex", "アニプレックス", "mappa", "bandai",
    "ponycanyon", "ponyキャニオン", "東映", "toei", "fujitv", "tbs",
    "noitamina", "ジャンプ", "jumpchannel", "kingrecord", "kingレコード",
    "sonymusic", "ソニーミュージック", "warnermusic", "ワーナー",
    "universalmusic", "ユニバーサル", "avex", "エイベックス",
    "lantis", "ランティス", "victorent", "ビクター",
)


def _has_token(text_norm: str, tokens: list[str]) -> bool:
    return any(t in text_norm for t in tokens)


def _is_distributor_channel(channel_norm: str) -> bool:
    return any(h in channel_norm for h in _OFFICIAL_CHANNEL_HINTS)


def is_official_video(
    channel_name: str,
    video_title: str,
    anime_title: str,
    artist: str,
) -> bool:
    """
    判定条件（いずれかを満たせば公式扱い）:
      1) チャンネル名に アニメタイトル or アーティスト名 のトークンが含まれる
      2) チャンネル名がアニメ配給／公式系のヒントを含み、動画タイトルに
         アニメタイトル と アーティスト名 の両方のトークンが含まれる
    """
    ch = _normalize(channel_name)
    if not ch:
        return False
    artist_toks = _artist_tokens(artist)
    anime_toks = _anime_tokens(anime_title)

    if _has_token(ch, artist_toks) or _has_token(ch, anime_toks):
        return True

    if _is_distributor_channel(ch):
        title_norm = _normalize(video_title)
        if anime_toks and artist_toks:
            if _has_token(title_norm, anime_toks) and _has_token(title_norm, artist_toks):
                return True
        elif anime_toks:
            if _has_token(title_norm, anime_toks):
                return True
    return False


# 後方互換用エイリアス
def is_official_channel(channel_name: str, anime_title: str, artist: str) -> bool:
    return is_official_video(channel_name, "", anime_title, artist)


def _build_query(song_title: str, artist: str, anime_title: str) -> str:
    parts: list[str] = []
    if song_title:
        parts.append(song_title)
    if artist:
        parts.append(artist)
    if anime_title:
        parts.append(anime_title)
    return " ".join(parts)


def _run_ytdlp(query: str) -> list[dict]:
    """yt-dlp 検索を実行し、候補のメタデータ list を返す。"""
    cmd = [
        "yt-dlp",
        f"ytsearch{SEARCH_RESULTS}:{query}",
        "--no-download",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--print",
        "%(.{id,title,channel,uploader,view_count,webpage_url})j",
    ]
    last_err: Exception | None = None
    for attempt in range(1, RETRY_MAX + 1):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT_SEC,
            )
            if proc.returncode != 0 and not proc.stdout.strip():
                raise RuntimeError(proc.stderr.strip() or f"yt-dlp returned {proc.returncode}")
            results: list[dict] = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return results
        except (subprocess.TimeoutExpired, RuntimeError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    print(f"[warn] yt-dlp failed after {RETRY_MAX} attempts: {last_err}")
    return []


def _select_best(results: list[dict], anime_title: str, artist: str) -> dict | None:
    """公式チャンネル候補のうち最高再生数を返す。なければ None。"""
    best: dict | None = None
    best_views = -1
    for r in results:
        ch = (r.get("channel") or r.get("uploader") or "")
        title = r.get("title") or ""
        if not is_official_video(ch, title, anime_title, artist):
            continue
        views = r.get("view_count") or 0
        try:
            views = int(views)
        except (TypeError, ValueError):
            views = 0
        if views > best_views:
            best_views = views
            best = r
    return best


def _cache_key(season_id: int, anime_title: str, kind: str, index: int, song_title: str, artist: str) -> str:
    return f"{season_id}|{anime_title}|{kind}{index}|{song_title}|{artist}"


def _is_cache_fresh(entry: dict) -> bool:
    ts = entry.get("searched_at")
    if not ts:
        return False
    try:
        when = dt.datetime.fromisoformat(ts)
    except ValueError:
        return False
    age = dt.datetime.now() - when
    if entry.get("url"):
        return age < dt.timedelta(days=FOUND_TTL_DAYS)
    return age < dt.timedelta(hours=NOT_FOUND_TTL_HOURS)


def _load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def load_cached_only(animes: list[Anime], data_dir: Path) -> dict[tuple, dict]:
    """検索を一切せず、既存キャッシュにあるものだけを返す。"""
    cache = _load_cache(data_dir / CACHE_FILENAME)
    out: dict[tuple, dict] = {}
    for anime in animes:
        for song in anime.songs:
            ckey = _cache_key(anime.season_id, anime.title, song.kind, song.index, song.title, song.artist)
            entry = cache.get(ckey)
            if entry and entry.get("url"):
                out[(anime.season_id, anime.title, song.kind, song.index)] = {
                    "url": entry["url"],
                    "views": entry.get("views", ""),
                    "channel": entry.get("channel", ""),
                    "searched_at": entry.get("searched_at", ""),
                }
    return out


def enrich_with_youtube(animes: list[Anime], data_dir: Path) -> dict[tuple, dict]:
    """
    各アニメの OP/ED について YouTube 公式動画を探し、
    {(season_id, anime_title, kind, index): {url, views, channel, searched_at}} を返す。
    """
    cache_path = data_dir / CACHE_FILENAME
    cache = _load_cache(cache_path)
    out: dict[tuple, dict] = {}

    total = sum(len(a.songs) for a in animes if not a.is_rerun)
    done = 0
    for anime in animes:
        if anime.is_rerun:
            # 再放送は曲が同じことが多く YouTube 側も既存。スキップしてキャッシュ節約
            continue
        for song in anime.songs:
            done += 1
            ckey = _cache_key(anime.season_id, anime.title, song.kind, song.index, song.title, song.artist)
            entry = cache.get(ckey)
            if entry and _is_cache_fresh(entry):
                if entry.get("url"):
                    out[(anime.season_id, anime.title, song.kind, song.index)] = {
                        "url": entry["url"],
                        "views": entry.get("views", ""),
                        "channel": entry.get("channel", ""),
                        "searched_at": entry["searched_at"],
                    }
                continue

            query = _build_query(song.title, song.artist, anime.title)
            print(f"[search {done}/{total}] {anime.title} {song.kind}{song.index}: {query[:80]}")
            results = _run_ytdlp(query)
            best = _select_best(results, anime.title, song.artist)
            now = dt.datetime.now().isoformat(timespec="seconds")
            if best:
                cache[ckey] = {
                    "url": best.get("webpage_url", ""),
                    "views": best.get("view_count", ""),
                    "channel": best.get("channel") or best.get("uploader", ""),
                    "title": best.get("title", ""),
                    "searched_at": now,
                }
                out[(anime.season_id, anime.title, song.kind, song.index)] = {
                    "url": cache[ckey]["url"],
                    "views": cache[ckey]["views"],
                    "channel": cache[ckey]["channel"],
                    "searched_at": now,
                }
                print(f"  → {cache[ckey]['channel']} ({cache[ckey]['views']} views)")
            else:
                cache[ckey] = {"url": "", "views": "", "channel": "", "searched_at": now}
                print(f"  → not found (will retry tomorrow)")
            time.sleep(THROTTLE_SEC)

    _save_cache(cache_path, cache)
    return out


if __name__ == "__main__":
    # 動作確認: 1件だけテスト
    from .anime_scraper import scrape_season
    label, animes = scrape_season(5228)
    sample = animes[:2]  # 上位2件
    out = enrich_with_youtube(sample, Path(__file__).resolve().parent.parent / "data")
    print(json.dumps({str(k): v for k, v in out.items()}, ensure_ascii=False, indent=2))
