"""エントリポイント: シーズン解決 → スクレイプ → YouTube → CSV/Excel → 差分検出。"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

from .anime_scraper import scrape_season
from .diff_reporter import report_diff
from .output_writer import write_outputs
from .season_finder import resolve_seasons

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="アニメ一覧を取得して CSV/Excel に出力")
    p.add_argument(
        "--season-id",
        type=int,
        action="append",
        help="個別にシーズンID指定（複数可）。--seasons より優先",
    )
    p.add_argument(
        "--seasons",
        default="current,next",
        help="current,next,all,spring,summer,autumn,winter のカンマ区切り",
    )
    p.add_argument(
        "--data-dir",
        default=str(DATA_DIR),
        help=f"出力先ディレクトリ（既定: {DATA_DIR}）",
    )
    p.add_argument(
        "--skip-youtube",
        action="store_true",
        help="YouTube 検索をスキップ",
    )
    p.add_argument(
        "--skip-diff",
        action="store_true",
        help="changelog 差分検出をスキップ",
    )
    return p.parse_args()


def resolve_targets(args: argparse.Namespace) -> list[tuple[str, int]]:
    if args.season_id:
        return [(f"id={sid}", sid) for sid in args.season_id]
    return resolve_seasons(args.seasons)


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # 差分検出のため事前に直前CSVを退避
    prev_dir = Path(tempfile.mkdtemp(prefix="anime_prev_"))
    prev_anime_csv = prev_dir / "anime_list.csv"
    prev_song_csv = prev_dir / "anime_songs.csv"
    if (data_dir / "anime_list.csv").exists():
        shutil.copy2(data_dir / "anime_list.csv", prev_anime_csv)
    if (data_dir / "anime_songs.csv").exists():
        shutil.copy2(data_dir / "anime_songs.csv", prev_song_csv)

    targets = resolve_targets(args)
    if not targets:
        print("[error] no seasons resolved")
        return 1

    all_animes = []
    for label, sid in targets:
        print(f"[info] fetching {label} (id={sid}) ...")
        season_label, animes = scrape_season(sid)
        print(f"  → {season_label}: {len(animes)} anime")
        all_animes.extend(animes)

    if args.skip_youtube:
        from .youtube_finder import load_cached_only
        youtube_map = load_cached_only(all_animes, data_dir)
        print(f"[info] skip-youtube: applied {len(youtube_map)} cached entries")
    else:
        from .youtube_finder import enrich_with_youtube
        youtube_map = enrich_with_youtube(all_animes, data_dir)

    csv_anime, csv_song, xlsx = write_outputs(all_animes, data_dir, song_youtube_map=youtube_map)
    print(f"[done] {csv_anime}")
    print(f"[done] {csv_song}")
    print(f"[done] {xlsx}")

    if not args.skip_diff:
        try:
            n = report_diff(data_dir, prev_anime_csv, prev_song_csv)
            if n > 0:
                print(f"[diff] {n} changes recorded in changelog.md")
            else:
                print("[diff] no changes")
        except Exception as e:
            print(f"[warn] diff failed: {e}")

    shutil.rmtree(prev_dir, ignore_errors=True)
    print(f"[done] generated_at={dt.datetime.now().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
