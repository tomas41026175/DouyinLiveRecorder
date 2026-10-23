# STATUS — DouyinLiveRecorder（個人客製 fork）

> 常駐型直播錄製：上游 DouyinLiveRecorder v4.0.7 + 時長追蹤、Web 控制台、健康監測、彈幕、回放、Discord。

| 欄位 | 值 |
|---|---|
| 狀態 | active |
| 部署位置 | Windows（安裝目錄 `F:\main\DouyinLiveRecorder_v4.0.7`；exe + web_ui） |
| 部署版本 | 待確認（exe 由 `build_main_exe.bat` 打包，web_ui 跑原始碼） |
| 最後驗證可用 | 待確認 |
| 資料位置 | 安裝目錄 `config\`（設定、recording_history.db、danmaku.db、marks.db） |
| 監控 | Healthchecks 心跳 `config/healthchecks.json`（程式已就緒，未設定）；alerts.py 本機警報 |
| 相關 repo | 上游 `ihmily/DouyinLiveRecorder`（`sync.yml` 每日同步）、`workbench`（總覽） |

## 目前進度

- 2026-10-23：Healthchecks 外部心跳（預設關閉）、unittest CI（#5）
- 2026-10-23：Windows 本機未提交的「各主播硬碟佔比」（`src/disk_share.py` + `/api/disk/share` + 儀表板）收進 main；Windows 原始碼資料夾改為追蹤 `myfork/main`
- 詳細功能進度見 `PROJECT_STATUS.md`，路線見 `ROADMAP.md`

## 下一步

1. Windows `git pull` 後重啟 web_ui，確認 Healthchecks `douyin-live-recorder` 轉綠；再 `build_main_exe.bat` + `deploy.bat`
2. ROADMAP P1-3 首次設定精靈
3. （待決）桌面 widget 已棄用，是否刪檔

## 已知問題

- 2026-09-24 ~ 10-23 Windows 原始碼資料夾沒有追蹤遠端，PR #1–#6 是手動複製或未部署；已修正追蹤，之後一律 `git pull`

- `resolve_all_segments()` 抓不到已壓縮分段檔（`tests/test_library.py` 以 expectedFailure 釘住）

## 決策紀錄

- `PROJECT_STATUS.md`、`ROADMAP.md`、`UIUX_SPEC.md`
