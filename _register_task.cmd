@echo off
rem 1時間おきの auto_pull タスクを登録する（再実行で上書き可）
schtasks /Delete /TN "AnimeListUpdater_AutoPull" /F >nul 2>&1
schtasks /Create /TN "AnimeListUpdater_AutoPull" /SC HOURLY /MO 1 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"E:\wk\02.AnimeRanking\anime_list_updater\auto_pull.ps1\"" /F
