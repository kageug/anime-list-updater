"""youtube_finder の動画採用ロジックの回帰テスト。

狙い (ユーザー要件):
  1. これまで正しく拾えていた動画は不変であること (= 既存の採用を取り消さない)。
  2. これまで弾かれていた「配給チャンネルのノンクレジットOP/ED映像」(曲名/歌手名が
     タイトルに無い高再生の公式OP/ED) だけが新たに拾えること。
  3. 本編PV / 別種別(OP↔ED) / 別番号(OP2) は引き続き弾くこと。

固定の検索結果(実データ由来)で _select_best の選択を検証する。ネットワーク不要。
"""
from src import youtube_finder as yf


def _c(channel, title, views, vid):
    return {
        "channel": channel,
        "title": title,
        "view_count": views,
        "webpage_url": f"https://www.youtube.com/watch?v={vid}",
        "id": vid,
    }


# ---- 実データ: 「ヒトリゴト / osage / ポンコツ風紀委員」OP の検索上位 ----
ANIME = "ポンコツ風紀委員とスカート丈が不適切なJKの話"
ARTIST = "osage"
SONG = "ヒトリゴト"

CREDITLESS_OP = _c("NBCUniversal Anime/Music",
    "TVアニメ『ポンコツ風紀委員とスカート丈が不適切なJKの話』ノンクレジットOP映像｜TOKYO MX(毎週日)23時30分～ほか",
    76076, "w7OmO94Ezt8")
ARTIST_MV = _c("osage official",
    "osage - ヒトリゴト [Music Video]ボーイズサイド(右)", 14692, "0XwlRpvjbmY")
HONPEN_PV = _c("NBCUniversal Anime/Music",
    "TVアニメ「ポンコツ風紀委員とスカート丈が不適切なJKの話」本編PV｜2026年4月6日(日)放送開始!", 82520, "4tGR6N92Hbw")
CREDITLESS_ED = _c("NBCUniversal Anime/Music",
    "TVアニメ『ポンコツ風紀委員とスカート丈が不適切なJKの話』ノンクレジットED映像｜", 18910, "jkUzjZlp0jk")
UNRELATED = _c("某歌ってみたチャンネル",
    "【歌ってみた】ヒトリゴト / osage 歌ってみた【ポンコツ風紀委員】", 196, "7ziTCTpjgH0")


def _vid(r):
    return r["id"] if r else None


# ---- 旧ロジック相当 (is_official_video のみ) の参照実装 ----
def _old_best_overall(results):
    best, best_v = None, -1
    for r in results:
        ch = r.get("channel") or r.get("uploader") or ""
        if yf.is_official_video(ch, r.get("title") or "", ANIME, ARTIST):
            v = int(r.get("view_count") or 0)
            if v > best_v:
                best_v, best = v, r
    return best


# ============================================================
# 1) 新規に拾えること: 76,076再生のノンクレジットOPが統計対象になる
# ============================================================
def test_creditless_op_now_picked_as_overall():
    results = [CREDITLESS_OP, ARTIST_MV, HONPEN_PV, CREDITLESS_ED, UNRELATED]
    overall, anime = yf._select_best(results, ANIME, ARTIST, SONG, kind="OP", song_index=1)
    assert _vid(overall) == "w7OmO94Ezt8"   # 高再生のノンクレOPを採用
    assert _vid(anime) == "w7OmO94Ezt8"     # アニメ版としても最適


def test_old_logic_missed_it():
    """旧ロジックでは osage MV(14,692)止まりだったことを固定。"""
    results = [CREDITLESS_OP, ARTIST_MV, HONPEN_PV, CREDITLESS_ED, UNRELATED]
    assert _vid(_old_best_overall(results)) == "0XwlRpvjbmY"


# ============================================================
# 2) 壊さないこと: ノンクレ候補が無ければ選択は旧ロジックと完全一致
# ============================================================
def test_no_regression_without_creditless():
    # ノンクレ映像を含まない集合 → kind 指定有無で結果が変わらない
    for results in (
        [ARTIST_MV, UNRELATED],
        [ARTIST_MV],
        [HONPEN_PV, ARTIST_MV, UNRELATED],   # PV は新ロジックでも除外
    ):
        new_overall, _ = yf._select_best(results, ANIME, ARTIST, SONG, kind="OP", song_index=1)
        assert _vid(new_overall) == _vid(_old_best_overall(results))


# ============================================================
# 3) 誤採用しないこと
# ============================================================
def test_honpen_pv_never_picked():
    # PV は再生数最大(82,520)だが曲ではないので採用しない
    results = [HONPEN_PV, ARTIST_MV]
    overall, _ = yf._select_best(results, ANIME, ARTIST, SONG, kind="OP", song_index=1)
    assert _vid(overall) == "0XwlRpvjbmY"


def test_wrong_kind_ed_not_picked_for_op():
    # OP曲の検索に ED 映像しか無ければ採用なし(別曲の取り違え防止)
    results = [CREDITLESS_ED]
    overall, anime = yf._select_best(results, ANIME, ARTIST, SONG, kind="OP", song_index=1)
    assert overall is None and anime is None


def test_multi_op_index_mismatch_rejected():
    op2 = _c("NBCUniversal Anime/Music",
        "TVアニメ『ポンコツ風紀委員とスカート丈が不適切なJKの話』ノンクレジットOP2映像", 99999, "OP2VIDEOxxx")
    # OP1 を探しているのに OP2 映像 → 採用しない
    overall, _ = yf._select_best([op2], ANIME, ARTIST, SONG, kind="OP", song_index=1)
    assert overall is None
    # 番号なしのノンクレOPは採用する
    overall2, _ = yf._select_best([CREDITLESS_OP], ANIME, ARTIST, SONG, kind="OP", song_index=1)
    assert _vid(overall2) == "w7OmO94Ezt8"


# ============================================================
# 4) 述語単体の確認
# ============================================================
def test_predicate_table():
    assert yf.is_creditless_kind_video(CREDITLESS_OP["channel"], CREDITLESS_OP["title"], ANIME, "OP", 1) is True
    assert yf.is_creditless_kind_video(HONPEN_PV["channel"], HONPEN_PV["title"], ANIME, "OP", 1) is False
    assert yf.is_creditless_kind_video(CREDITLESS_ED["channel"], CREDITLESS_ED["title"], ANIME, "OP", 1) is False
    # ED曲としてなら ED映像は採用可
    assert yf.is_creditless_kind_video(CREDITLESS_ED["channel"], CREDITLESS_ED["title"], ANIME, "ED", 1) is True
    # 既存の公式判定(チャンネル名に歌手名)は不変
    assert yf.is_official_video(ARTIST_MV["channel"], ARTIST_MV["title"], ANIME, ARTIST) is True
