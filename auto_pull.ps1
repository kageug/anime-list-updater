# auto_pull.ps1
# anime-list-updater の git pull --ff-only をログ付きで実行する。
# Windows タスクスケジューラから 1 時間おきに呼び出す想定。

$ErrorActionPreference = 'Continue'
$repo = 'D:\work\30_ani_KPOP_JPOP\anime_list_updater'
$logDir = Join-Path $repo 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ('auto_pull_{0}.log' -f (Get-Date -Format 'yyyyMM'))

$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"--- $ts ---" | Out-File -FilePath $log -Append -Encoding utf8

Set-Location $repo
$out = git pull --ff-only 2>&1
$out | Out-File -FilePath $log -Append -Encoding utf8
