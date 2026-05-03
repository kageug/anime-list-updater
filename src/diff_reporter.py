"""前回出力 CSV と現状を比較し、changelog.md に差分を追記する。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

CHANGELOG_FILENAME = "changelog.md"


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False, dtype=str)
    except Exception as e:  # 壊れていたら空とみなす
        print(f"[warn] failed to read {path}: {e}")
        return pd.DataFrame()


def _anime_key(row: pd.Series) -> str:
    return f"{row.get('season_id','')}|{row.get('title','')}|{row.get('is_rerun','')}"


def _song_key(row: pd.Series) -> str:
    return (
        f"{row.get('season_id','')}|{row.get('anime_title','')}|"
        f"{row.get('kind','')}{row.get('index','')}|{row.get('song_title','')}"
    )


def diff_anime_lists(prev: pd.DataFrame, curr: pd.DataFrame) -> dict:
    """新規アニメ／削除アニメを抽出。"""
    if prev.empty:
        return {"added": curr.to_dict("records") if not curr.empty else [], "removed": []}
    prev_keys = {_anime_key(r): r for _, r in prev.iterrows()}
    curr_keys = {_anime_key(r): r for _, r in curr.iterrows()}
    added = [curr_keys[k] for k in curr_keys.keys() - prev_keys.keys()]
    removed = [prev_keys[k] for k in prev_keys.keys() - curr_keys.keys()]
    return {"added": added, "removed": removed}


def diff_song_lists(prev: pd.DataFrame, curr: pd.DataFrame) -> dict:
    """OP/ED の YouTube URL が新規発見されたものを抽出。"""
    found_youtube: list[dict] = []
    if curr.empty:
        return {"new_youtube": found_youtube}
    if prev.empty:
        for _, r in curr.iterrows():
            if r.get("youtube_url"):
                found_youtube.append(r.to_dict())
        return {"new_youtube": found_youtube}

    prev_map: dict[str, dict] = {_song_key(r): r.to_dict() for _, r in prev.iterrows()}
    for _, r in curr.iterrows():
        url = r.get("youtube_url", "")
        if not url:
            continue
        prev_row = prev_map.get(_song_key(r))
        if not prev_row or not prev_row.get("youtube_url"):
            found_youtube.append(r.to_dict())
    return {"new_youtube": found_youtube}


def write_changelog(data_dir: Path, anime_diff: dict, song_diff: dict) -> tuple[Path, int]:
    """changelog.md の先頭にエントリを追記。戻り値: (path, change_count)"""
    added = anime_diff["added"]
    removed = anime_diff["removed"]
    new_yt = song_diff["new_youtube"]
    change_count = len(added) + len(removed) + len(new_yt)
    if change_count == 0:
        return data_dir / CHANGELOG_FILENAME, 0

    today = dt.date.today().isoformat()
    lines: list[str] = [f"## {today}", ""]
    if added:
        lines.append(f"### 新規アニメ ({len(added)} 件)")
        for r in added:
            label = r.get("season_label", "")
            title = r.get("title", "")
            rerun = "（再放送）" if str(r.get("is_rerun", "")).lower() in ("true", "1") else ""
            lines.append(f"- [{label}] {title}{rerun}")
        lines.append("")
    if removed:
        lines.append(f"### 削除アニメ ({len(removed)} 件)")
        for r in removed:
            label = r.get("season_label", "")
            title = r.get("title", "")
            lines.append(f"- [{label}] {title}")
        lines.append("")
    if new_yt:
        lines.append(f"### 新規YouTube動画発見 ({len(new_yt)} 件)")
        for r in new_yt:
            anime = r.get("anime_title", "")
            kind = f"{r.get('kind','')}{r.get('index','')}"
            song = r.get("song_title", "")
            artist = r.get("artist", "")
            ch = r.get("youtube_channel", "")
            url = r.get("youtube_url", "")
            lines.append(f"- [{anime}] {kind} 「{song}」{artist} → {ch} {url}")
        lines.append("")

    new_block = "\n".join(lines) + "\n"

    path = data_dir / CHANGELOG_FILENAME
    existing = path.read_text(encoding="utf-8") if path.exists() else "# 更新履歴\n\n"
    # ヘッダ行 "# 更新履歴" の直後に挿入
    if existing.startswith("# "):
        first_nl = existing.find("\n\n")
        if first_nl == -1:
            merged = existing + "\n" + new_block
        else:
            merged = existing[: first_nl + 2] + new_block + existing[first_nl + 2:]
    else:
        merged = "# 更新履歴\n\n" + new_block + existing

    path.write_text(merged, encoding="utf-8")
    return path, change_count


def report_diff(data_dir: Path, prev_anime_csv: Path, prev_song_csv: Path) -> int:
    """直前のCSVと現在のCSVを比較し、changelog.mdを更新。差分件数を返す。"""
    curr_anime = _load_csv(data_dir / "anime_list.csv")
    curr_song = _load_csv(data_dir / "anime_songs.csv")
    prev_anime = _load_csv(prev_anime_csv)
    prev_song = _load_csv(prev_song_csv)
    a_diff = diff_anime_lists(prev_anime, curr_anime)
    s_diff = diff_song_lists(prev_song, curr_song)
    _, n = write_changelog(data_dir, a_diff, s_diff)
    return n
