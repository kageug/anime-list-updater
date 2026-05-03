# anime_list_updater

アニメイトタイムズのシーズン特集ページから新作アニメ一覧と OP/ED を抽出し、yt-dlp で公式 YouTube 動画を検索して CSV / Excel に書き出すツール。GitHub Actions で 1 日 1 回自動更新する想定。

- データソース: `https://www.animatetimes.com/tag/details.php?id={season_id}`
- シーズン (春/夏/秋/冬) のタグ ID はサイトナビから自動取得 → 季節が変わっても URL を書き換えずに追従
- 新作アニメは公式 YouTube チャンネルが後から開設されることが多いため、毎日リトライしてキャッチ

## 出力ファイル (`data/`)

| ファイル | 内容 |
|---|---|
| `anime_list.csv` | アニメ 1 件 1 行（マスタ） |
| `anime_songs.csv` | OP/ED 1 曲 1 行（YouTube URL 付き） |
| `anime_list.xlsx` | 上記 2 CSV を 2 シート化した Excel |
| `youtube_cache.json` | YouTube 検索キャッシュ（再検索抑制） |
| `changelog.md` | 日次差分（追加アニメ・新規 YouTube 発見） |

## ローカル実行

```powershell
cd d:\work\30_ani_KPOP_JPOP\anime_list_updater
pip install -r requirements.txt

# 現行シーズン + 次期シーズンを取得（既定）
python -m src.main

# シーズンを明示
python -m src.main --seasons spring,summer
python -m src.main --season-id 5228          # ID 直接指定

# YouTube 検索だけスキップ（高速）
python -m src.main --skip-youtube
```

## 公式チャンネル判定ロジック

以下のいずれかを満たす YouTube 動画を「公式」とみなし、複数該当時は再生数最大を採用：

1. チャンネル名にアニメタイトル or アーティスト名のトークンが含まれる
   - 例: アーティスト名「PompadollS」 → チャンネル名「PompadollS」 ✓
2. チャンネル名がアニメ配給／公式系のヒント語（"アニメ", "公式", "official", "KADOKAWA", "TOHO", "アニプレックス" 等）を含み、**動画タイトル**にアニメ名 + アーティスト名が両方含まれる
   - 例: 「日活アニメチャンネル」 → 動画タイトルに「愛してるゲームを終わらせたい」「CHiCO with HoneyWorks」両方あり ✓

カナ／英字差・全角半角差・空白／記号差は正規化して比較。

## キャッシュポリシー

- ヒット済みは 7 日間再検索しない
- 未ヒットは 22 時間で期限切れ → 翌日のジョブで再試行（新規開設チャンネルを早めにキャッチ）

## GitHub Actions による自動化

`.github/workflows/update.yml` が毎日 06:00 JST (= 21:00 UTC) に走り、差分があれば自動コミット & プッシュ。

### 初回セットアップ

```powershell
cd d:\work\30_ani_KPOP_JPOP\anime_list_updater
git init
git add .
git commit -m "initial"

# GitHub CLI で公開リポジトリを作成（要 gh auth login 済）
gh repo create anime-list-updater --public --source=. --remote=origin --push
```

これだけで毎日自動で `data/` 配下が更新される。

### 手動トリガ

GitHub の Actions タブ → 「Update anime list」 → `Run workflow`。
`seasons` 入力で季節を切り替え可能。

### ローカル Windows での代替（GitHub を使わない場合）

タスクスケジューラに登録：
```powershell
schtasks /create /tn "AnimeListUpdate" /sc daily /st 06:00 /tr "powershell -NoProfile -Command \"cd 'd:\work\30_ani_KPOP_JPOP\anime_list_updater'; python -X utf8 -m src.main\""
```

## モジュール構成

| ファイル | 役割 |
|---|---|
| `src/anime_scraper.py` | アニメイトタイムズタグページ → アニメ + OP/ED 抽出 |
| `src/season_finder.py` | ナビメニューから春夏秋冬タグ ID を発見、現行/次期シーズン解決 |
| `src/youtube_finder.py` | yt-dlp で OP/ED 動画検索 + 公式判定 + 再生数最大選択 |
| `src/output_writer.py` | CSV (UTF-8 BOM) と 2 シート Excel 書き出し |
| `src/diff_reporter.py` | 直前 CSV と比較 → 新規アニメ／新規 YouTube 動画を `changelog.md` に追記 |
| `src/main.py` | オーケストレータ (CLI エントリ) |

## 検証手順

1. **シーズン自動発見** — 春夏秋冬 4 件のタグ ID が出る
   ```powershell
   python -X utf8 -m src.season_finder
   ```
2. **スクレイパー単体** — 100 件前後のアニメが見える
   ```powershell
   python -X utf8 -m src.anime_scraper 5228
   ```
3. **エンドツーエンド** — `data/anime_list.csv` が生成され、一部の OP/ED に YouTube URL が入る
   ```powershell
   python -X utf8 -m src.main --season-id 5228
   ```
4. **差分検出** — 同じコマンドを 2 回目に走らせる前に CSV を編集 → `changelog.md` にエントリが入る

## 既知の制約

- **複数 OP/ED**: `anime_list.csv` 側は最初の OP/ED のみ表記。詳細は `anime_songs.csv` 参照
- **再放送**: `is_rerun=True`。曲が同じなことが多く YouTube 検索はスキップ
- **YouTube 検索失敗時**: 翌日リトライで自動キャッチアップ（新規開設チャンネル待ちを想定）
- **アニメ詳細ページ取得**: 不要（タグ一覧ページに OP/ED が含まれている）
