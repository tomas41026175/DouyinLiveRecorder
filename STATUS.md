# STATUS — DouyinLiveRecorder（個人客製 fork）

> 常駐型直播錄製：上游 DouyinLiveRecorder v4.0.7 + 時長追蹤、Web 控制台、健康監測、彈幕、回放、Discord。

| 欄位 | 值 |
|---|---|
| 狀態 | active |
| 部署位置 | Windows（安裝目錄 `F:\main\DouyinLiveRecorder_v4.0.7`；exe + web_ui） |
| 部署版本 | 待確認（exe 由 `build_main_exe.bat` 打包，web_ui 跑原始碼） |
| 最後驗證可用 | 2026-10-23（Healthchecks 心跳轉綠） |
| 資料位置 | 安裝目錄 `config\`（設定、recording_history.db、danmaku.db、marks.db） |
| 監控 | Healthchecks `douyin-live-recorder`（5 分鐘 / 寬限 10 分鐘，通知 Discord）；alerts.py 本機警報 |
| 相關 repo | 上游 `ihmily/DouyinLiveRecorder`（`sync.yml` 每日只檢查、不自動合併）、`workbench`（總覽） |

## 目前進度

- 2026-10-24：`/api/streamers` 每位主播多回 `last_recorded_at`（`MAX(start_time)`，含正在錄的那場；總時長／場次仍只算已結束），純邏輯在 `src/streamer_stats.py`；只動 Web UI，重啟 web_ui 即生效
- 2026-10-24：修正主播硬碟容量顯示 0 B：`/api/streamers`、`/api/disk/share` 改由錄製紀錄的 `file_path` 反推主播資料夾（`src/anchor_folder.py`），只動 Web UI，重啟 web_ui 即生效
- 2026-10-24：補上 `CHANGELOG.md`、`docs/adr/0001-architecture.md` 與「回滾」段落
- 2026-10-24：上游同步改為只檢查（`sync.yml`）：有新 commit 時開／更新 issue 或寫執行摘要，不再自動併進 main
- 2026-10-23：Healthchecks 外部心跳（預設關閉）、unittest CI（#5）
- 2026-10-23：Windows 本機未提交的「各主播硬碟佔比」（`src/disk_share.py` + `/api/disk/share` + 儀表板）收進 main；Windows 原始碼資料夾改為追蹤 `myfork/main`
- 2026-10-23：Windows 拉到最新版，Healthchecks 心跳上線並串接 Discord
- 詳細功能進度見 `PROJECT_STATUS.md`，路線見 `ROADMAP.md`

## 下一步

1. `build_main_exe.bat` + `deploy.bat`，讓錄影 exe 用上 09-24 之後的修正（孤兒 ffmpeg 等）
2. ROADMAP P1-3 首次設定精靈
3. （待決）桌面 widget 已棄用，是否刪檔

## 已知問題

- 2026-09-24 ~ 10-23 Windows 原始碼資料夾沒有追蹤遠端，PR #1–#6 是手動複製或未部署；已修正追蹤，之後一律 `git pull`

- `resolve_all_segments()` 抓不到已壓縮分段檔（`tests/test_library.py` 以 expectedFailure 釘住）

## 回滾

在 Windows 原始碼資料夾 `F:\main\DouyinLiveRecorder_v4.0.7\source_with_duration_tracker\`（追蹤 `myfork/main`）：

1. `git log --oneline` 找到要回去的 commit，`git checkout <commit>`（或 `git revert <壞的 commit>` 後 `git pull`）。
2. 只動到 Web UI 側（`web_ui.py`、`web_static\`、web 用的 `src\*`）：重啟 web_ui（`start_console.bat`）即可。
3. 動到 recorder（`main.py`、`src\spider.py`、`src\stream.py`、`src\duration_tracker.py` 等）：`build_main_exe.bat` 重建 → `deploy.bat`
   （會先停程式、把 `config\` 備份到安裝目錄 `config_backup_<時間>`，再覆蓋 exe 與 `_internal\` 並重啟）。
4. 設定壞掉：從安裝目錄最近的 `config_backup_<時間>\` 複製回 `config\`（錄影檔不受影響）。
5. 只想先停外部心跳：關掉 heartbeat 設定（預設關閉），Healthchecks 會在寬限後轉紅，屬預期。

待確認：`recording_history.db`、`danmaku.db`、`marks.db` 的 schema 若被新版改過，舊版是否能讀（目前沒有降版測試）。

## 決策紀錄

- 2026-10-24 上游同步只檢查不合併：原 `aormsby/Fork-Sync-With-Upstream-action` 每天把上游自動併進 main，2026-10-24 在同步步驟 exit 128 失敗（當時上游落後 0、本 fork 領先 22）。本 fork 大量客製且 Windows 依 main 部署，自動合併等於未審查上線；改為每天比較、由人決定是否手動合併（`git merge upstream/main` → 測試 → PR）
- 架構彙整：`docs/adr/0001-architecture.md`；變更紀錄：`CHANGELOG.md`
- `PROJECT_STATUS.md`、`ROADMAP.md`、`UIUX_SPEC.md`
