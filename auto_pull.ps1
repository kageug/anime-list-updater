# auto_pull.ps1
# anime-list-updater の git pull --ff-only をログ付きで実行する。
# Windows タスクスケジューラから 1 時間おきに呼び出す想定。

$ErrorActionPreference = 'Continue'
$repo = 'E:\wk\02.AnimeRanking\anime_list_updater'
$logDir = Join-Path $repo 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ('auto_pull_{0}.log' -f (Get-Date -Format 'yyyyMM'))

$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"--- $ts ---" | Out-File -FilePath $log -Append -Encoding utf8

Set-Location $repo
$out = git pull --ff-only 2>&1
$out | Out-File -FilePath $log -Append -Encoding utf8

# pull で更新があれば、anison.db / master_video に新規曲を取り込む
if ($out -match 'Already up to date\.') {
    "no changes - skip ingest" | Out-File -FilePath $log -Append -Encoding utf8
} else {
    "running ingest-anime-list ..." | Out-File -FilePath $log -Append -Encoding utf8
    Set-Location 'E:\wk\02.AnimeRanking'
    $ing = & python -m anison_rankin ingest-anime-list 2>&1
    $ing | Out-File -FilePath $log -Append -Encoding utf8
}
