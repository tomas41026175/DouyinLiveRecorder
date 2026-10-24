# 0001 — 目前架構：Recorder exe + Web UI 雙執行體、安裝目錄 config\ 共享、標準庫優先

- 狀態：accepted
- 日期：2026-10-24（整理既有決策；細節以連結的原文件為準）
- 廢棄原因：—

## 背景

本 repo 是個人自用、單機常駐的直播錄製工具：上游 DouyinLiveRecorder v4.0.7 加上時長追蹤、Web 控制台、健康監測、磁碟管理、
H.265 壓縮、彈幕、回放標記、Discord 通知與控制頻道（`AGENT.md` §1）。部署在 Windows，安裝目錄 `F:\main\DouyinLiveRecorder_v4.0.7`。

這份 ADR 只彙整已存在、散在各文件的決策，不新增決策。

## 決策

1. **兩個獨立執行體**：Recorder（`main.py` 經 PyInstaller 打包成 `DouyinLiveRecorder.exe`）與 Web UI（Flask `web_ui.py` 直接跑原始碼）。
   改 recorder 用的程式碼要 `build_main_exe.bat` 重建 exe；改 web 側只要重啟 web_ui（`AGENT.md` §2）。
2. **兩者用共享檔案溝通**：`config\URL_config.ini`、`config\recording_history.db`（recorder 寫、web_ui 讀）、`config\streamer_limits.json`（`AGENT.md` §2）。
3. **執行階段設定一律在安裝目錄 `config\`**，不是原始碼子資料夾 `source_with_duration_tracker\` 裡那份（`AGENT.md` §3）。
4. **不加第三方依賴，標準庫優先**：recorder 用精簡可攜 Python（`pyembed\`）；唯一例外是 Discord 控制頻道用的 `discord.py`（2026-08-31 經使用者同意，`AGENT.md` §5-5）。
5. **純邏輯與 IO 分離、預設安全、不靜默失敗、JSON 設定 + `_deep_merge`**（`AGENT.md` §5-1～4），每個新功能附單元測試（§5-6、§8）。
6. **`.bat` 一律純 ASCII**：繁中 Windows 用 Big5 讀 .bat，UTF-8 中文會壞（`CLAUDE.md`、`deploy.bat` 檔頭）。
7. **上游同步只檢查、不自動合併**（2026-10-24，`STATUS.md` 決策紀錄）。
8. **外部監控用 Healthchecks 心跳**：`douyin-live-recorder`（5 分鐘 / 寬限 10 分鐘，通知 Discord），本機另有 `alerts.py` 警報（`STATUS.md`）。

## 替代方案

| 方案 | 不選的原因 |
|---|---|
| 每天自動把上游併進 main | 本 fork 大量客製且 Windows 依 main 部署，自動合併等於未審查上線；2026-10-24 也曾 exit 128 失敗（`STATUS.md`） |
| 自由引入第三方套件 | recorder 跑精簡可攜 Python，依賴越少越好；`discord.py` 以外需先徵得同意（`AGENT.md` §5-5） |
| 待確認 | 雙執行體（而非 recorder 也跑原始碼）的原始取捨沒有文件記錄 |

## 後果

- 改 `main.py` 或 recorder 用的 `src/*` 後忘了重建 exe，會以為沒生效（`AGENT.md` §2 最常見的坑）。
- 部署版本難以一眼看出：exe 與 web_ui 可能不同版（`STATUS.md` 部署版本：待確認）。
- 上游新功能要人工合併：`git merge upstream/main` → 測試 → PR。
