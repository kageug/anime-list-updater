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


def _song_tokens(song_title: str) -> list[str]:
    """曲名から照合用トークンを取り出す。正規化済み全文を 1 トークンとして返す。"""
    if not song_title:
        return []
    n = _normalize(song_title)
    if len(n) >= 2:
        return [n]
    return []


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

# 「アニメ版動画」とみなすチャンネルヒント（アニメ寄り限定）
# レコード会社系（ソニーミュージック、ワーナー、エイベックス等）は除外。
_ANIME_CHANNEL_HINTS = (
    "アニメ", "anime", "アニプレックス", "aniplex",
    "kadokawa", "ジャンプ", "jumpchannel", "noitamina",
    "東映", "toei", "ぽにきゃん", "ponycanyon",
    "mappa", "bandai", "lantis", "ランティス",
    "tvアニメ", "tv anime",
)

# 動画タイトルに含まれていれば「アニメ版OP/ED映像」と強く判定する語
# (ノンクレジット = アニメ会社のスタッフロールなしOP/ED 公式アップ)
_ANIME_VERSION_TITLE_HINTS = (
    "ノンクレジット", "ノンテロップ", "ノンテロ",
    "non credit", "non-credit", "noncredit",
    "creditless", "credit-less",
    "クレジットなし", "クレジット無し",
    "tvサイズ", "tv size", "テレビアニメ",
    "オープニング映像", "エンディング映像",
    "opening movie", "ending movie",
)


def _has_token(text_norm: str, tokens: list[str]) -> bool:
    return any(t in text_norm for t in tokens)


def _is_distributor_channel(channel_norm: str) -> bool:
    return any(h in channel_norm for h in _OFFICIAL_CHANNEL_HINTS)


def _is_anime_channel(channel_norm: str) -> bool:
    return any(h in channel_norm for h in _ANIME_CHANNEL_HINTS)


def _title_has_anime_version_hint(video_title_norm: str) -> bool:
    return any(_normalize(h) in video_title_norm for h in _ANIME_VERSION_TITLE_HINTS)


def is_anime_video(
    channel_name: str,
    video_title: str,
    anime_title: str,
    artist: str,
    song_title: str = "",
) -> bool:
    """「アニメ版動画」(=切り抜き素材として使えるアニメ寄り動画) 判定。
    判定条件 (アニメ性):
      1) チャンネル名にアニメタイトルのトークンが含まれる
      2) チャンネル名がアニメ寄り配給ヒントを含み、かつ動画タイトルに
         アニメタイトルのトークンが含まれる
      3) 動画タイトルに「ノンクレジット」「Non-Credit」等の明示的なアニメ版
         キーワードが含まれ、かつ動画タイトルにアニメタイトルのトークンが含まれる

    曲レベル一致 (song_title or artist が分かっている場合):
      動画タイトルに 曲名 か 歌手名トークン のいずれかが含まれていなければ拒否。
      同一アニメの別曲のノンクレ動画を誤って拾うのを防ぐ。
    """
    ch = _normalize(channel_name)
    if not ch:
        return False
    anime_toks = _anime_tokens(anime_title)
    if not anime_toks:
        return False
    title_norm = _normalize(video_title)

    is_anime_match = False
    if _has_token(ch, anime_toks):
        is_anime_match = True
    elif _is_anime_channel(ch) and _has_token(title_norm, anime_toks):
        is_anime_match = True
    elif _title_has_anime_version_hint(title_norm) and _has_token(title_norm, anime_toks):
        is_anime_match = True
    if not is_anime_match:
        return False

    # 曲レベル一致: 曲名 / 歌手名トークンの少なくとも一方が動画タイトルにあること
    song_toks = _song_tokens(song_title)
    artist_toks = _artist_tokens(artist)
    if song_toks or artist_toks:
        if not (_has_token(title_norm, song_toks) or _has_token(title_norm, artist_toks)):
            return False
    return True


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


# kind 別のセカンダリ検索ヒント（強い順）
_SECONDARY_HINTS = {
    "OP": ["ノンクレジットOP", "ノンクレジット オープニング", "オープニング映像"],
    "ED": ["ノンクレジットED", "ノンクレジット エンディング", "エンディング映像"],
    "主題歌": ["ノンクレジット 主題歌", "主題歌 映像"],
    "IN": ["挿入歌"],
}
SECONDARY_QUERY_LIMIT = 2  # セカンダリは最大2回試す


def _build_secondary_queries(song_title: str, artist: str, anime_title: str, kind: str) -> list[str]:
    """アニメ版動画を狙ったセカンダリ検索クエリ群を優先順に返す。

    曲名が極端に短い (3文字以下) 場合は曲名単独だと一般語にヒットしやすいので
    必ずアニメ名を併記する。アーティスト名は混入させない（アーティストMVを引きにくくする）。
    """
    hints = _SECONDARY_HINTS.get(kind) or _SECONDARY_HINTS["OP"]
    is_short = len((song_title or "").replace(" ", "")) <= 3
    queries: list[str] = []
    for hint in hints[:SECONDARY_QUERY_LIMIT]:
        # アニメ名 + 曲名 + ヒント (アーティスト省略)
        parts = [anime_title, song_title, hint] if is_short or anime_title else [song_title, hint]
        q = " ".join(p for p in parts if p).strip()
        if q:
            queries.append(q)
    return queries


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


def _select_best(
    results: list[dict],
    anime_title: str,
    artist: str,
    song_title: str = "",
) -> tuple[dict | None, dict | None]:
    """検索結果から
      - best_overall: 公式判定 (アーティスト/アニメどちらも可) で最高再生数 (= 統計対象, A列)
      - best_anime:   アニメ寄り動画で最高再生数 (= アニメ版動画, H列)
    を返す。best_overall が既にアニメ動画なら best_anime は同じものを指す。
    """
    best_overall: dict | None = None
    best_overall_views = -1
    best_anime: dict | None = None
    best_anime_views = -1
    for r in results:
        ch = (r.get("channel") or r.get("uploader") or "")
        title = r.get("title") or ""
        views = r.get("view_count") or 0
        try:
            views = int(views)
        except (TypeError, ValueError):
            views = 0

        if is_official_video(ch, title, anime_title, artist):
            if views > best_overall_views:
                best_overall_views = views
                best_overall = r
        if is_anime_video(ch, title, anime_title, artist, song_title):
            if views > best_anime_views:
                best_anime_views = views
                best_anime = r
    return best_overall, best_anime


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


def _entry_to_out(entry: dict) -> dict:
    """キャッシュ entry → 出力辞書（A列+H列両方含む）。"""
    return {
        "url": entry.get("url", ""),
        "views": entry.get("views", ""),
        "channel": entry.get("channel", ""),
        "searched_at": entry.get("searched_at", ""),
        "anime_url": entry.get("anime_url", ""),
        "anime_views": entry.get("anime_views", ""),
        "anime_channel": entry.get("anime_channel", ""),
    }


def load_cached_only(animes: list[Anime], data_dir: Path) -> dict[tuple, dict]:
    """検索を一切せず、既存キャッシュにあるものだけを返す。"""
    cache = _load_cache(data_dir / CACHE_FILENAME)
    out: dict[tuple, dict] = {}
    for anime in animes:
        for song in anime.songs:
            ckey = _cache_key(anime.season_id, anime.title, song.kind, song.index, song.title, song.artist)
            entry = cache.get(ckey)
            if entry and entry.get("url"):
                out[(anime.season_id, anime.title, song.kind, song.index)] = _entry_to_out(entry)
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
            # 旧キャッシュ（anime_url キー無し）は再検索対象とする
            has_anime_field = entry is not None and "anime_url" in entry
            secondary_tried = entry is not None and entry.get("anime_secondary_tried")
            cache_complete = (
                entry is not None
                and (not entry.get("url") or has_anime_field)
                and (entry.get("anime_url") or secondary_tried)
            )
            if entry and _is_cache_fresh(entry) and cache_complete:
                if entry.get("url"):
                    out[(anime.season_id, anime.title, song.kind, song.index)] = _entry_to_out(entry)
                continue

            # キャッシュにプライマリ結果がある（url埋まり）かつ anime_url が空、
            # かつまだ secondary を試していない場合: プライマリは流用しセカンダリだけ実行
            if (entry and entry.get("url") and not entry.get("anime_url")
                    and not secondary_tried and _is_cache_fresh(entry)):
                primary_anime = None  # 既にプライマリで見つからなかった
                for sec_q in _build_secondary_queries(song.title, song.artist, anime.title, song.kind):
                    print(f"[search {done}/{total} retry-anime] {anime.title} {song.kind}{song.index}: {sec_q[:80]}")
                    sec_results = _run_ytdlp(sec_q)
                    _, sec_anime = _select_best(sec_results, anime.title, song.artist, song.title)
                    if sec_anime is not None:
                        primary_anime = sec_anime
                        break
                    time.sleep(THROTTLE_SEC)
                entry["anime_secondary_tried"] = True
                if primary_anime is not None:
                    entry["anime_url"] = primary_anime.get("webpage_url", "")
                    entry["anime_views"] = primary_anime.get("view_count", "")
                    entry["anime_channel"] = primary_anime.get("channel") or primary_anime.get("uploader", "")
                    print(f"  → anime: {entry['anime_channel']} ({entry['anime_views']} views)")
                else:
                    print(f"  → anime: not found")
                cache[ckey] = entry
                out[(anime.season_id, anime.title, song.kind, song.index)] = _entry_to_out(entry)
                time.sleep(THROTTLE_SEC)
                continue

            query = _build_query(song.title, song.artist, anime.title)
            print(f"[search {done}/{total}] {anime.title} {song.kind}{song.index}: {query[:80]}")
            results = _run_ytdlp(query)
            best_overall, best_anime = _select_best(results, anime.title, song.artist)

            # プライマリでアニメ版が見つからなかった場合、ノンクレジット系
            # キーワードでセカンダリ検索を最大 SECONDARY_QUERY_LIMIT 回試す
            if best_overall is not None and best_anime is None:
                for sec_q in _build_secondary_queries(song.title, song.artist, anime.title, song.kind):
                    time.sleep(THROTTLE_SEC)
                    print(f"  [retry anime] {sec_q[:80]}")
                    sec_results = _run_ytdlp(sec_q)
                    _, sec_anime = _select_best(sec_results, anime.title, song.artist, song.title)
                    if sec_anime is not None:
                        best_anime = sec_anime
                        break

            now = dt.datetime.now().isoformat(timespec="seconds")
            secondary_was_tried = best_overall is not None  # 上で best_overall ありなら secondary を回した
            if best_overall or best_anime:
                # アニメ版が見つかっていて A列(統計対象) が無い場合は A列にもアニメ版を入れる
                primary = best_overall or best_anime
                # best_overall が既にアニメ寄り動画なら H列 = A列（"右に同じ"）
                anime_pick = best_anime
                if best_overall and best_anime and best_overall.get("webpage_url") == best_anime.get("webpage_url"):
                    anime_pick = best_overall

                cache[ckey] = {
                    "url": primary.get("webpage_url", ""),
                    "views": primary.get("view_count", ""),
                    "channel": primary.get("channel") or primary.get("uploader", ""),
                    "title": primary.get("title", ""),
                    "anime_url": anime_pick.get("webpage_url", "") if anime_pick else "",
                    "anime_views": anime_pick.get("view_count", "") if anime_pick else "",
                    "anime_channel": (anime_pick.get("channel") or anime_pick.get("uploader", "")) if anime_pick else "",
                    "anime_secondary_tried": secondary_was_tried,
                    "searched_at": now,
                }
                out[(anime.season_id, anime.title, song.kind, song.index)] = _entry_to_out(cache[ckey])
                ch = cache[ckey]["channel"]
                v = cache[ckey]["views"]
                ach = cache[ckey]["anime_channel"]
                if ach and ach != ch:
                    print(f"  → {ch} ({v} views) | anime: {ach}")
                elif ach:
                    print(f"  → {ch} ({v} views) [= anime]")
                else:
                    print(f"  → {ch} ({v} views)")
            else:
                cache[ckey] = {
                    "url": "", "views": "", "channel": "",
                    "anime_url": "", "anime_views": "", "anime_channel": "",
                    "searched_at": now,
                }
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
