"""アニメ一覧 / 曲一覧を CSV と Excel に書き出す。"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from .anime_scraper import Anime, Song

ANIME_COLUMNS = [
    "season_id",
    "season_label",
    "order",
    "title",
    "is_rerun",
    "broadcast_format",
    "broadcast_start",
    "animation_studio",
    "op_title",
    "op_artist",
    "ed_title",
    "ed_artist",
    "detail_url",
]

SONG_COLUMNS = [
    "season_id",
    "season_label",
    "anime_title",
    "is_rerun",
    "kind",        # OP / ED / 主題歌
    "index",       # 1, 2, ...
    "song_title",
    "artist",
    "youtube_url",
    "youtube_views",
    "youtube_channel",
    "last_searched_at",
]


def _first_song(songs: list[Song], kind: str) -> Song | None:
    for s in songs:
        if s.kind == kind:
            return s
    return None


def to_anime_rows(animes: Iterable[Anime]) -> list[dict]:
    rows = []
    for a in animes:
        op = _first_song(a.songs, "OP")
        ed = _first_song(a.songs, "ED")
        rows.append({
            "season_id": a.season_id,
            "season_label": a.season_label,
            "order": a.order,
            "title": a.title,
            "is_rerun": a.is_rerun,
            "broadcast_format": a.broadcast_format,
            "broadcast_start": a.broadcast_start,
            "animation_studio": a.animation_studio,
            "op_title": op.title if op else "",
            "op_artist": op.artist if op else "",
            "ed_title": ed.title if ed else "",
            "ed_artist": ed.artist if ed else "",
            "detail_url": a.detail_url,
        })
    return rows


def to_song_rows(animes: Iterable[Anime]) -> list[dict]:
    rows = []
    for a in animes:
        for s in a.songs:
            rows.append({
                "season_id": a.season_id,
                "season_label": a.season_label,
                "anime_title": a.title,
                "is_rerun": a.is_rerun,
                "kind": s.kind,
                "index": s.index,
                "song_title": s.title,
                "artist": s.artist,
                "youtube_url": "",
                "youtube_views": "",
                "youtube_channel": "",
                "last_searched_at": "",
            })
    return rows


def write_outputs(
    animes: list[Anime],
    data_dir: Path,
    *,
    song_youtube_map: dict[tuple, dict] | None = None,
) -> tuple[Path, Path, Path]:
    """
    CSV (anime_list, anime_songs) と Excel を data_dir に書き出す。
    song_youtube_map: {(season_id, anime_title, kind, index): {url, views, channel, ts}}
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    anime_rows = to_anime_rows(animes)
    song_rows = to_song_rows(animes)

    if song_youtube_map:
        for row in song_rows:
            key = (row["season_id"], row["anime_title"], row["kind"], row["index"])
            yt = song_youtube_map.get(key)
            if yt:
                row["youtube_url"] = yt.get("url", "")
                row["youtube_views"] = yt.get("views", "")
                row["youtube_channel"] = yt.get("channel", "")
                row["last_searched_at"] = yt.get("searched_at", "")

    df_anime = pd.DataFrame(anime_rows, columns=ANIME_COLUMNS)
    df_song = pd.DataFrame(song_rows, columns=SONG_COLUMNS)

    csv_anime = data_dir / "anime_list.csv"
    csv_song = data_dir / "anime_songs.csv"
    xlsx = data_dir / "anime_list.xlsx"

    df_anime.to_csv(csv_anime, index=False, encoding="utf-8-sig")
    df_song.to_csv(csv_song, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        df_anime.to_excel(writer, sheet_name="anime", index=False)
        df_song.to_excel(writer, sheet_name="songs", index=False)

    return csv_anime, csv_song, xlsx
