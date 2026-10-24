# 變更紀錄

本 fork（上游 ihmily/DouyinLiveRecorder v4.0.7 + 自訂層）的變更紀錄，格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。
本 fork 沒有打版本 tag，依合併日期（PR 編號）記錄；2026-09 以前的自訂功能見 `PROJECT_STATUS.md`、`ROADMAP.md`。

## 2026-10-24

### 新增
- `/api/streamers` 每筆多回 `last_recorded_at`（ISO 字串或 null）：最近一場錄製的開始時間，含正在錄的那場；`total_sec`/`sessions` 口徑不變（`src/streamer_stats.py`）

### 修正
- 主播硬碟容量顯示 0 B：`/api/streamers` 的 `disk_bytes`/`folder_path` 與 `/api/disk/share` 改由錄製紀錄 `file_path` 反推實際主播資料夾（`src/anchor_folder.py`），共用資料夾時只算該主播自己的檔案（含 H.265 壓縮後檔名）

### 變更
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
