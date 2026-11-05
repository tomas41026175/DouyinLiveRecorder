# 變更紀錄

本 fork（上游 ihmily/DouyinLiveRecorder v4.0.7 + 自訂層）的變更紀錄，格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。
本 fork 沒有打版本 tag，依合併日期（PR 編號）記錄；2026-09 以前的自訂功能見 `PROJECT_STATUS.md`、`ROADMAP.md`。

## 2026-11-05

### 修正
- 主播名含半形／全形逗號時錄影程式讀錯 `URL_config.ini` 那一行（`main.py` 用 `re.split('[,，]', line)` 切整行：3 段時網址被當成名稱、那位主播沒在錄；4 段以上丟例外、那一輪清單後面的行都讀不到）（workbench#107、#56）：web_ui 寫檔時把主播名的逗號換成底線（新增、編輯改名、啟用切換、批量、Discord 新增都走 `entry_to_line`）；`/api/streamers` 多回 `name_has_comma`，主播管理對既有的壞行標警告並附「改名」捷徑。純邏輯在 `src/url_config_line.py`（`tests/test_url_config_line.py`）。不動 `main.py`、不用重建 exe

## 2026-11-06

### 新增
- 臨時錄製 API（`src/adhoc_record.py`、`tests/test_adhoc_record.py`）：`/api/live/record/start|<id>/heartbeat|<id>/stop`、`GET /api/live/record`；不加入監控清單，30 秒沒心跳自動停，停止後 `.ts` 轉 `.mp4`（`<直播保存路径>/手動錄製/<主播>/`），同時 3 場、每場 6 小時上限，Windows Job Object 讓 ffmpeg 跟著 web_ui 結束；log：`logs/adhoc_record.log`、`logs/adhoc_ffmpeg.log`

## 2026-11-04

### 修正
- 直播觀看 `/api/live/source` 找不到直播間：帶設定檔 cookie 時抖音回不出資料（不帶就正常）→ 先帶 cookie、失敗再不帶；短網址先拿 `owner.web_rid` 換成 `live.douyin.com/<web_rid>`；只有拋例外才回 502，其餘當「未開播或抖音號不存在」；未開播只快取 10 秒（`live_view.is_live_page`／`live_url_from_room`／`cookie_attempts`／`pick_room`）

### 新增
- 直播即時觀看 API（給 workbench `/rec/live`）：`GET /api/live/source?q=` 以抖音號／直播間網址／App 分享文字解析直播間，回各畫質 HLS 網址（一律 https，含純音訊）；`POST /api/live/watch` 觀看中心跳，把沒在錄的直播間併入彈幕擷取（2 分鐘沒心跳移除、最多 6 間）；`GET /api/live/danmaku?url=&since=` 增量彈幕。純邏輯在 `src/live_view.py`（`tests/test_live_view.py`）

## 2026-10-29

### 變更
- `.gitignore` 加 `config/*.db-wal`、`config/*.db-shm`：SQLite WAL／SHM 不再出現在 `git status`，自動更新不會因此判定工作區不乾淨（workbench#52）

## 2026-10-24

### 新增
- `upstream-spider-sync.yml`：每天把上游對 `AUTO_UPDATE_FILES`（`src/spider.py`）的變更 3-way merge 進 fork 開 PR，required checks 全綠自動 squash 合併；`test.yml`、`status-gate.yml` 支援 `workflow_dispatch`（`tools/upstream_file_sync.py`、`.github/upstream-sync.json`）
- `/api/streamers` 每筆多回 `last_recorded_at`（ISO 字串或 null）：最近一場錄製的開始時間，含正在錄的那場；`total_sec`/`sessions` 口徑不變（`src/streamer_stats.py`）

### 修正
- 主播硬碟容量顯示 0 B：`/api/streamers` 的 `disk_bytes`/`folder_path` 與 `/api/disk/share` 改由錄製紀錄 `file_path` 反推實際主播資料夾（`src/anchor_folder.py`），共用資料夾時只算該主播自己的檔案（含 H.265 壓縮後檔名）

### 變更
- web_ui 背景更新檢查預設只檢查、不覆蓋 `src/spider.py`（`config/update_settings.json` 的 `auto_apply` 可開回）；「版本 / 更新」頁說明新流程，手動套用前警告會讓工作區變 dirty；`.gitignore` 加 `src/*.upstream-bak`
- 上游同步改為只檢查、不自動合併：`sync.yml` 每日比較上游，有新 commit 時開／更新 issue 或寫執行摘要（#9）

## 2026-10-23

### 新增
- Healthchecks 外部心跳（`src/heartbeat.py`，預設關閉）與 stdlib unittest CI（#5）
- `STATUS.md` 與 status-gate workflow（改程式碼必須同步更新 STATUS）（#6）
- 各主播硬碟佔比：`src/disk_share.py` + `/api/disk/share` + 儀表板（#7）

### 文件
- STATUS 更新：Healthchecks 心跳上線、監控與下一步（#8）

## 2026-09-28

### 新增
- 磁碟用量歷史與預估寫滿時間（ETA）（#4）

## 2026-09-27

### 新增
- 主播改名後的資料夾合併（#2）
- URL 前綴處理（#3）

## 2026-09-25

### 修正
- 錄製器結束後殘留的孤兒 ffmpeg 程序（#1）
