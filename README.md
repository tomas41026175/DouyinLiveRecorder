# DouyinLiveRecorder — 自訂版

基於上游 [ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) v4.0.7，加上：

- **錄製時長追蹤** — 每場錄製寫進 SQLite，可查單一主播 / 每日 / 累計
- **Web 控制台** — 側邊欄導覽（監控／內容／設定三大類），瀏覽器管理主播清單、查看錄製狀態、設定上限
- **錄製時長控制** — 設定每位主播每天最長錄幾分鐘，達上限自動停（隔天重置）
- **GitHub 自動更新** — 每 6 小時檢查 upstream `spider.py`，有新版自動套用
- **磁碟用量追蹤** — Web UI 顯示每位主播資料夾佔多少空間
- **批量操作** — checkbox 批量啟用 / 停用 / 刪除主播
- **開機自啟** — 一鍵把 recorder + Web UI 裝進 startup folder（桌面小工具已棄用，見下方說明）
- **系統健康監測 + 主動警報** — 磁碟滿 / 錄製器停止 / 資料庫異常 / 錄製疑似損壞 / cookie 過期時，透過 ntfy / Telegram / Webhook 主動通知（含防洗版節流）
- **磁碟自動管理** — 剩餘空間過低自動暫停新錄製；選用的自動清理最舊錄影（預設關閉）
- **錄完自動壓縮** — 錄製完成的檔案自動轉 H.265，體積約省 40–60%；驗證時長一致才刪原檔，錄製中檔案絕不碰。支援**獨顯 + 內顯 + CPU 多編碼器並行**（各壓不同檔案），可手動勾選要用哪幾個；設定頁另有**壓縮占比總覽**（全部/已壓縮/未壓縮位元組數 + 累計已省空間）與獨立的「立即開始壓縮」手動觸發（不受自動開關影響）
- **Port 自動避讓** — 8765 被占用時自動改用 8766–8770
- **log 保留** — web_ui 主控台輸出輪替保留近 5 份，故障不因重啟消失
- **特別關注** — 標記的主播每 60 秒偵測一次開播（一般 300 秒），第一時間開錄
- **彈幕擷取 / 查詢 / 轉字幕** — 擷取直播聊天彈幕、依主播/關鍵字查詢、產出旁載 .ass/.srt 字幕
- **錄影回放** — 控制台依日期連續播放當天所有錄影，自動接下一段；右側彈幕欄跟播捲動 + 點擊跳轉 + 關鍵字搜尋，也可載入旁載字幕
- **統計頁** — 主播錄製時長排行榜、今日/本月/全部歷史摘要、單一主播明細、匯出 CSV
- **錄製設定頁** — 控制台內直接改 `config.ini`（畫質、保存路徑、分資料夾規則、影片格式、循環/分段秒數、Cookie），存檔會標示是否需要重啟錄製器
- **Discord 錄製通知** — 主播開始/結束錄製時發 Discord embed 卡片（多位合併、含開始時間/時長）
- **回放標記** — 回放頁可在目前播放位置加標記（時間點或一段時間段 + 備註），點擊直接跳轉；可跨同一場分段錄製 session 的相鄰片段
- **Discord 控制頻道** — 在指定的 Discord 頻道用文字指令或斜線指令（`/add` `/remove` `/list` `/help`）新增/移除監控中的主播，不用開控制台；新增主播也支援直接貼抖音號（不一定要完整網址）

## 文件導覽

| 檔案 | 內容 |
|------|------|
| **README.md**（本檔） | 使用者操作說明：安裝、各功能怎麼用、疑難排解 |
| **AGENT.md** | 開發／維護指南：架構、慣例、build/deploy、陷阱（改碼前先讀） |
| **CLAUDE.md** | 給 AI agent 的精簡須知（指向 AGENT.md） |
| **ROADMAP.md** | 產品優先級、已完成項目與待辦 |
| **PROJECT_STATUS.md** | 階段性進度快照 / 交接 |

---

## 一鍵安裝

雙擊 **`install_all.bat`** — 全自動：
1. 下載可攜版 Python 3.11（10MB）→ `pyembed\`
2. 安裝所有 deps + PyInstaller
3. 重新打包 `DouyinLiveRecorder.exe` → 部署到上層
4. 啟動 recorder + Web UI（瀏覽器控制台就是完整介面，不需要另外的桌面小工具）

完成後，再雙擊 **`install_autostart.bat`** 設定登入時自動啟動。

---

## 目錄架構

```
source_with_duration_tracker/
├── main.py                       上游 recorder + duration tracker hooks
├── main.spec                     PyInstaller 設定
├── requirements.txt              主程式依賴
│
├── web_ui.py                     Flask 後端
├── widget.py                     【已棄用】系統匣 tray icon，功能已被瀏覽器控制台取代
├── widget_window.py              【已棄用】pywebview 浮動視窗，同上
├── query_duration.py             CLI 工具：查時長排行
│
├── ffmpeg_install.py             上游：ffmpeg 自動安裝
├── i18n.py                       上游：翻譯載入
├── msg_push.py                   上游：訊息推送（微信/TG/email/…）
│
├── install_all.bat               一鍵安裝 + 重打包 + 部署 + 啟動
├── install_autostart.bat         把 recorder / Web UI 放進 Startup（不裝 widget）
├── uninstall_autostart.bat       取消開機自啟
├── build_main_exe.bat            僅重打包 .exe（如改了 main.py / spider.py）
├── deploy.bat                    一鍵部署新 exe（停舊→備份 config→覆蓋→重啟）
├── start_console.bat            【建議，日常請用這個】輕量啟動：recorder + web_ui + 自動開瀏覽器
├── start_widget.bat              【已棄用】kill+restart 整套（widget + web_ui），保留給仍想用舊浮動視窗的人手動執行
├── start_webui_silent.bat        僅啟動 web_ui（給 autostart 用）
│
├── AGENT.md                      開發／維護指南（架構、慣例、陷阱）
├── CLAUDE.md                     AI agent 精簡須知
├── ROADMAP.md / PROJECT_STATUS.md  路線圖 / 進度快照
├── query_duration.py             CLI：查錄製時長排行
├── query_danmaku.py              CLI：查彈幕 / 產字幕（--subtitle）
│
├── config/
│   ├── URL_config.ini            主播清單（# 註解 = 停用）
│   ├── config.ini                錄製設定（畫質、保存路徑、cookies…）
│   ├── recording_history.db      SQLite，每場錄製明細
│   ├── streamer_limits.json      每位主播的上限 / 備註 / 冷卻狀態
│   ├── alerts.json               健康警報設定 + 磁碟自動管理策略
│   ├── webui_port.txt            web_ui 實際綁定的 port（舊版 widget 曾讀這個，現無使用者）
│   ├── compress.json             錄完自動壓縮設定（enabled / crf / preset…）
│   ├── compress_state.json       壓縮統計 + 跳過清單（自動產生）
│   ├── danmaku.db                彈幕資料庫（SQLite）
│   ├── marks.db                  回放標記資料庫（SQLite）
│   ├── rec_notify.json           Discord 錄製通知設定
│   ├── discord_bot.json          Discord 控制頻道機器人設定（token/頻道 ID）
│   └── update_state.json         GitHub 更新檢查狀態
│
├── i18n/                         語系檔（en, zh_CN）
├── logs/                         錄製器 log
│
├── src/
│   ├── __init__.py
│   ├── duration_tracker.py       時長追蹤（SQLite）+ 崩潰回收
│   ├── update_checker.py         GitHub 自動更新
│   ├── health.py                 健康評估（磁碟/錄製器/DB/壞檔/cookie）純邏輯
│   ├── alerts.py                 主動警報派送（ntfy/TG/Webhook）+ 節流/恢復通知
│   ├── disk_manager.py           磁碟自動管理（暫停/清理規劃，純邏輯）
│   ├── compressor.py             錄完自動壓縮（H.265 多編碼器並行）
│   ├── netutil.py                port 偵測 + webui_port.txt 讀寫
│   ├── danmaku_store.py          彈幕 SQLite 儲存 / 查詢（純邏輯）
│   ├── danmaku_capture.py        抖音彈幕 websocket 擷取（best-effort）
│   ├── danmaku_subtitle.py       彈幕→ASS/SRT 字幕（純邏輯）
│   ├── library.py                錄影庫：日期分組 + 檔案解析 + 播放清單
│   ├── marks_store.py            回放標記 SQLite 儲存（純邏輯）
│   ├── rec_notify.py             Discord 錄製通知（開始/結束 embed 卡片）
│   ├── discord_bot.py            Discord 控制頻道機器人（新增/移除主播；唯一用到 discord.py 這個第三方依賴）
│   ├── spider.py                 上游：直播 API 爬蟲（會自動更新）
│   ├── stream.py                 上游：取流網址
│   ├── room.py                   上游：房間解析
│   ├── utils.py                  上游：工具函式
│   ├── ab_sign.py                上游：抖音 a-bogus 簽名
│   ├── proxy.py                  上游：proxy 偵測
│   ├── logger.py                 上游：log 設定
│   ├── initializer.py            上游：啟動初始化
│   ├── http_clients/             上游：http 套件
│   └── javascript/               上游：JS 加密腳本
│
├── web_static/
│   ├── index.html                Web UI 主頁（新版側邊欄控制台）
│   └── widget.html               【已棄用】浮動視窗用的精簡版，功能已併入主控台
│
└── pyembed/                      可攜版 Python（install_webui.bat 產生）
```

---

## 用 Web UI

`install_all.bat` 跑完後（或平常用 **`start_console.bat`**），**瀏覽器自動開** `http://127.0.0.1:8765/`

> 若 8765 被占用，web_ui 會自動改用 8766–8770，實際 port 寫進 `config/webui_port.txt`。
> 實際網址看 web_ui 主控台印出的 `Listening` 那行，或控制台頂部 topbar 的 port 顯示。

控制台是**側邊欄導覽**（左側常駐，分「監控 / 內容 / 設定」三組），沒有分頁切換的概念：

**監控**
- **儀表板** — KPI 卡（監測中/錄製中/主播總數/磁碟用量/剩餘空間）+ 系統健康 panel（綠/黃/紅健康燈，hover 看問題明細）+ 磁碟使用率 panel + 正在錄製清單（進度條 + 立即停止）+ 壓縮佇列 mini panel（有工作才顯示）+「↻ 立即檢查開播」
- **主播管理** — 全部主播，checkbox 多選（收進「批量 ▾」下拉）+ 啟用/停用 toggle + 編輯 + 刪除 + 排序 + 自動恢復時間欄位 + 特別關注星號

**內容**
- **彈幕查詢** — 依主播/關鍵字/發言者/日期篩選、分頁瀏覽、匯出 CSV、彈幕轉字幕（頁首 panel）
- **錄影回放** — 依日期連續播放，右側第 4 欄是**同步彈幕**：跟播捲動 + 目前行高亮、點彈幕跳轉播放位置、關鍵字搜尋跳轉、可收合、時間戳/發言者顯示開關；與既有 VTT 字幕疊字並存。播放器下方可**加標記**（時間點或時間段 + 備註），點擊直接跳轉，可跨同一場分段錄製的相鄰片段
- **統計**（新） — 主播錄製時長排行榜、篩選全部歷史/本月/今日、摘要（總時長/場次/平均/最長場次）、點主播展開單人明細、匯出 CSV

**設定**（5 個常駐子頁，取代舊版彈出式「⚙ 通知設定」面板）
- **錄製設定**（新） — 直接改 `config.ini`：畫質、保存路徑、分資料夾規則（依作者/時間/標題）、檔名含標題、影片格式、循環/分段秒數、Cookie（依平台動態列出，收合面板）；存檔會標示是否需要重啟錄製器
- **通知** — 健康警報（ntfy/Telegram/Webhook）+ Discord 錄製通知 + Discord 控制頻道（文字指令新增/移除主播），三者共用一頁
- **壓縮** — 自動壓縮開關/編碼器/CRF/preset 等 + 壓縮占比總覽（全部/已壓縮/未壓縮位元組數、累計已省空間）+ 獨立的「▶ 立即開始壓縮」（即使自動壓縮關閉也能手動觸發）
- **磁碟** — 磁碟自動暫停/自動清理最舊錄影策略
- **系統** — 版本/更新狀態、目前設定摘要（保存路徑、分資料夾規則等）

topbar（跨頁常駐）：健康燈 + 目前 port + 手動刷新；側邊欄底部有錄製器狀態與磁碟剩餘的迷你常駐顯示。

---

## 設定每位主播的錄製上限

Web UI → 側邊欄「主播管理」→ 點該行「編輯」：
- **單場錄製上限（分鐘）** — 超過自動停
- **達上限後自動恢復時間（分鐘）** — 不填 = 隔天 00:00 重置；填數字 = 過 N 分鐘恢復

每 15 秒檢查一次。主播清單表格有「自動恢復(分)」欄位可一覽各主播設定；未設定顯示「—」。
工具列「**清空自動恢復時間**」按鈕可一鍵把所有主播的此欄位清空。

---

## 特別關注（每分鐘檢測開播）

一般主播依 `config.ini` 的「循环时间(秒)」（預設 300 秒）輪詢是否開播。
對於想第一時間抓到開播的主播，可設為「**特別關注**」，偵測間隔縮短為 **60 秒**。

設定方式（兩種）：
- 主播清單每列的「關注」欄點 **☆ / ★** 星號即可切換
- 或編輯主播時勾選「★ 每分鐘檢測開播」

旗標存在 `streamer_limits.json` 的 `"priority": true`，recorder 端依檔案 mtime 快取。

> ⚠️ **此功能在 `main.py` 內，必須重建 exe 才生效**：改過 `main.py`（或第一次加入此功能）後，執行 `build_main_exe.bat` 重新打包 `DouyinLiveRecorder.exe` 並部署到上層，再重啟錄製器。控制台的星號/欄位是即時的，但**實際 60 秒輪詢由 exe 決定**——若發現特別關注沒在一分鐘內開錄，多半是 exe 還是舊版沒重建。

---

## 錄製通知（Discord）

主播**開始 / 結束錄製**時，發通知到 Discord 頻道。

### 步驟 1：在 Discord 取得 Webhook URL

前提：你要有該伺服器的**管理員**或「管理 Webhook」權限。

電腦版：

1. 進到要接收通知的**伺服器**，把滑鼠移到目標**文字頻道**（例如 `#錄製通知`）→ 點右側**齒輪圖示**（編輯頻道）；或右鍵頻道 → 「編輯頻道」。
2. 左側選單 → 「**整合 / Integrations**」。
3. 「**Webhook**」→ 「**建立 Webhook / New Webhook**」（可改名字，例如「錄製通知」）。
4. 點該 Webhook → 「**複製 Webhook 網址 / Copy Webhook URL**」。

手機版：伺服器 → 長按頻道 → 編輯頻道 → Webhook → 建立 Webhook → 複製網址。

複製到的 URL 格式：`https://discord.com/api/webhooks/<數字>/<一長串>`

> ⚠️ 這串 URL 等於一把發言鑰匙，**別公開**；任何拿到的人都能往該頻道發訊息。想停用就回到同一處**刪除 Webhook**。找不到「整合」選項＝你在該伺服器沒有管理權限。

### 步驟 2：在控制台設定頁貼上

1. 開控制台 → 側邊欄「**設定 → 通知**」→「**錄製通知（Discord）**」區塊。
2. 勾「**啟用錄製通知**」、勾「**Discord Webhook**」並貼上 URL、選要通知的時機（開始 / 結束）。
3. 按「**儲存設定**」→ 按「**送出測試通知**」→ Discord 頻道應立刻收到「🔔 DLR 測試通知」。

之後訊息範例：`🔴 開始錄製：**主播名**` + 直播間連結、`⏹ 結束錄製：**主播名**（時長 1h23m）`。

技術：由 web_ui 每 60 秒的背景輪詢比對「正在錄製」清單的變化來觸發，偵測到開播後最多 60 秒內發出，**不需重建 exe**。設定存 `config/rec_notify.json`，預設關閉。（也支援通用 webhook。）

疑難排解：測試通知沒收到 → 多半是 URL 貼錯/該 Webhook 被刪、或忘了按「儲存設定」。設定頁該區塊會顯示送出結果訊息。

---

## Discord 控制頻道（新增/移除主播）

在指定的 Discord 頻道用文字指令直接新增/移除監控中的主播，不用開控制台網頁。**這跟上面的「錄製通知」是完全不同的東西**——錄製通知只是單向發訊息（Webhook 就夠），這個功能需要**常駐監聽**頻道訊息，所以要建立一個真正的 Discord Bot（不是 Webhook）。

### 步驟 1：建立 Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications) → 「**New Application**」→ 取個名字（例如「DLR 主播控制」）。
2. 左側選單「**Bot**」→ 「**Add Bot**」（如果還沒有的話）。
3. 同一頁往下找「**Privileged Gateway Intents**」，打開「**MESSAGE CONTENT INTENT**」並存檔——**這步最容易漏掉**，沒打開的話 bot 收不到訊息文字內容，指令永遠不會有反應。
4. 「**Reset Token**」（或 Bot 頁面上的 Token）→ 複製起來，等一下要貼到控制台（這是機密，跟密碼一樣，別外流）。

### 步驟 2：把 Bot 邀請進你的伺服器

1. 左側選單「**OAuth2 → URL Generator**」。
2. **SCOPES** 勾 `bot` **和** `applications.commands`（後者是斜線指令 `/add` `/remove` `/list` `/help` 能同步出來的必要條件，只勾 `bot` 的話斜線指令不會出現）；下面出現的 **BOT PERMISSIONS** 勾 `Send Messages`、`Read Message History`。
3. 複製產生的網址，瀏覽器打開、選你的伺服器、授權。
4. 如果 bot 之前只用 `bot` scope 邀請過、現在要補斜線指令：用上面新產生的網址重新走一次授權流程即可（不用先把 bot 踢出伺服器）。

### 步驟 3：取得頻道 ID

Discord 設定 → 進階設定 → 打開「**開發者模式**」→ 回到伺服器，右鍵你想用來控制的頻道 → 「**複製頻道 ID**」。**建議另外開一個專用頻道**（例如 `#主播控制`），因為這個頻道裡任何人打對指令都能新增/移除主播，設好 Discord 頻道權限限制誰看得到這個頻道。

### 步驟 4：在控制台設定頁填入

1. 開控制台 → 側邊欄「**設定 → 通知**」→「**Discord 控制頻道（新增/移除主播）**」區塊。
2. 貼上 **Bot Token**、**頻道 ID**，指令前綴預設 `!`（可改）。
3. 勾「**啟用控制頻道機器人**」→ 按「**儲存並重新連線**」。狀態顯示「🟢 已連線」代表成功。

### 指令

在剛剛設定的頻道打字，文字指令（記得加前綴，預設 `!`）跟斜線指令（打 `/` 會跳出選單、有參數提示）都能用，效果完全一樣：

| 文字指令 | 斜線指令 | 說明 |
|------|------|------|
| `!新增 <網址或抖音號> [主播名稱]` | `/add url:<網址或抖音號> name:<主播名稱>` | 新增一位主播；網址可以是完整的 `https://live.douyin.com/...`，也可以直接打抖音號（沒有 `http` 開頭就自動補成 `https://live.douyin.com/<抖音號>`） |
| `!移除 <主播名稱或網址片段>` | `/remove target:<主播名稱或網址片段>` | 先精確比對主播名稱，比對不到才退而求其次比對網址是否包含這段文字；符合多筆會列出來請你打更精確 |
| `!清單` | `/list` | 列出目前設定的所有主播（🔴 錄製中 / ⚪ 待命 / ⏸️ 已停用） |
| `!help` | `/help` | 顯示指令說明 |

斜線指令第一次要等 bot 連上後自動同步一次才會出現在 `/` 選單裡（連上當下的 log 會印「已同步 N 個斜線指令」），且**邀請 bot 時要有勾 `applications.commands` scope**（見上面步驟 2），只勾 `bot` 的舊邀請連結需要重新走一次授權。

疑難排解：
- 打指令 bot 完全沒反應 → 先確認步驟 1-3 是否有 MESSAGE CONTENT INTENT、bot 是否真的在該伺服器、頻道 ID 有沒有貼對。
- 設定頁狀態一直顯示「連線中…」或有錯誤訊息 → 多半是 Token 貼錯/已失效，回 Developer Portal 重新 Reset Token。
- 改了設定按「儲存並重新連線」後狀態沒變 → 舊連線斷開是非同步的，等幾秒再重新整理頁面看狀態；還是不行就重啟 web_ui。

---

## 系統健康監測 + 主動警報

控制台儀表板頂部有**健康狀態膠囊**（綠=正常 / 黃=注意 / 紅=嚴重），列出當前問題。
背景 watchdog 每 60 秒評估一次，狀態惡化或恢復時透過推播通知你。

偵測項目：

- **磁碟空間** — 低於警告/嚴重門檻
- **錄製器行程** — DouyinLiveRecorder.exe 是否還活著（有啟用主播時才算問題）
- **資料庫** — recording_history.db 是否可讀寫（磁碟滿會壞）
- **錄製完整性** — 近 24h 是否有疑似損壞的錄製（0 byte / 過短 / 錯誤）。**已歸檔判定**：錄很久又正常結束、只是檔案事後被移走/上傳網盤刪掉的，**不算**損壞（避免大量誤報）；只有秒退＋無檔、或檔案還在卻被截斷、或 error/crash 才會標記
- **Cookie 過期** — 錯誤訊息疑似認證/登入失敗，指名是哪個平台

**設定方式**：側邊欄「**設定 → 通知**」→ 啟用警報、填 ntfy 主題 / Telegram / Webhook →「送出測試通知」確認收得到。
設定存在 `config/alerts.json`，**預設關閉**，不填不影響既有行為。

---

## 磁碟自動管理

側邊欄「**設定 → 磁碟**」：

- **剩餘低於 N GB 暫停新錄製** — 防止磁碟被錄到 100%（含 hysteresis 防抖，回到「恢復門檻」才解除）
- **自動清理最舊錄影**（**預設關閉，危險操作**）— 啟用後可設定保留目標 GB、永遠保留最近 N 天、保護錄製中的檔案。可先「預覽清理」看會刪哪些，再「立即清理」。

策略存在 `config/alerts.json` 的 `disk` 區段。儀表板的磁碟 panel 會顯示即時使用率 bar 與剩餘空間摘要。

---

## 錄完自動壓縮（省空間）

web_ui 背景每 10 分鐘掃描保存資料夾，把**已錄完**的影片轉成 H.265（mp4），體積約省 40–60%、畫質幾乎不變。

安全機制（確保只壓「錄製完整」的檔案）：

- 檔案 **15 分鐘沒再寫入**才視為錄製完成（錄製中 / 轉檔中的檔案 mtime 持續更新，絕不會被碰）
- 先輸出成 `xxx_hevc.part.mp4`，**ffmpeg 成功 + 時長與原檔一致（誤差 ≤ max(2 秒, 1%)）+ 確實變小**，三項全過才改名為 `xxx_hevc.mp4` 並刪除原檔；任一失敗則保留原檔
- 已是 H.265、太小、或先前失敗過的檔案會記在 `config/compress_state.json`，不重複嘗試
- ffmpeg 以「低於正常」CPU 優先權執行，不搶錄製資源；剩餘空間不足以容納輸出時自動跳過

**設定方式**：側邊欄「**設定 → 壓縮**」（啟用開關、編碼器、CRF 畫質、速度 preset、靜置分鐘、最小檔案 MB），可「▶ 立即開始壓縮」立即處理（**這個按鈕獨立於自動開關**——即使「啟用自動壓縮」關著也能手動觸發一輪）、或「暫停壓縮／恢復壓縮」隨時暫停佇列。**暫停會立即中止進行中的壓縮**，該檔原檔完整保留、不標記失敗，恢復後重試。進度區顯示目前進度與累計節省空間；同頁還有「**壓縮總覽（占比）**」panel，顯示全部/已壓縮/未壓縮的檔案數與位元組數占比、累計已省空間（60 秒快取）。設定存在 `config/compress.json`。

### 多編碼器並行（獨顯 + 內顯 + CPU 同時壓）

啟動時會自動偵測這台 ffmpeg 支援哪些 H.265 編碼器：

| 編碼器 | ffmpeg | 適用 |
|--------|--------|------|
| NVIDIA 獨顯 | `hevc_nvenc` | 有 NVIDIA 顯卡 |
| Intel 內顯 | `hevc_qsv` | Intel Core 內建顯卡（QuickSync） |
| AMD 內顯/顯卡 | `hevc_amf` | AMD APU / 顯卡 |
| CPU | `libx265` | 一定可用、最相容 |

**同一個檔案不會拆給多個編碼器**（會破壞接縫畫質）；而是每個編碼器各自從同一個佇列拿**不同的檔案**同時壓，吞吐量隨可用編碼器數量倍增。面板「使用的編碼器」可：

- **勾「自動」** — 用偵測到的全部硬體編碼器（＋可選 CPU）
- **手動勾選** — 自己指定目前要並行哪幾個（例：只勾 AMD 內顯 + CPU 兩條線）

進度區會顯示**總進度條**（完成 N/總數、目前並行幾條線）外加**每條編碼線各一條進度條**（顯示該線正在壓哪個檔、百分比、耗時）。硬體編碼器若實際執行失敗，該檔會自動退回 CPU 壓，不會卡住。設定欄位：`enabled_encoders`（手動清單，空＝自動）、`use_cpu_lane`、`max_lanes`。

> 注意：多線同時讀寫大檔，**硬碟 I/O 可能成為瓶頸**；若硬碟是傳統 HDD，並行收益可能不如 SSD 明顯。

---

## 彈幕查詢（弹幕）

控制台「**彈幕查詢**」分頁可查詢直播聊天彈幕：依主播、關鍵字、發言者、起始日期篩選，分頁瀏覽、匯出 CSV。也有 CLI：

```cmd
python query_danmaku.py --anchors                     列出有彈幕的主播
python query_danmaku.py --anchor "京圈太子"           查某主播彈幕
python query_danmaku.py --keyword "晚安"              關鍵字搜尋
python query_danmaku.py --keyword "抽獎" --export logs/dm.csv
```

彈幕存在 `config/danmaku.db`（SQLite）。

**彈幕轉字幕**：彈幕分頁「彈幕轉字幕」區塊，選一支已完成的錄影 →「產生字幕」，會在影片旁產生兩個同名旁載檔（不動原影片）：

- `影片名.danmaku.ass` — 滾動彈幕（多行右→左飛過，像原生彈幕；用 PotPlayer/mpv/VLC 播放會自動載入）
- `影片名.danmaku.srt` — 底部字幕（同一瞬間多則會併成一格；可選含發言者名稱）

時間對齊：彈幕的 unix 時間戳減去該場錄影的開始時間＝在影片中的出現秒數；超出影片範圍的彈幕自動略過。CLI 亦可：

```cmd
python query_danmaku.py --subtitle "F:\path\影片.mp4" --start 2026-06-24T20:00:00 --duration 3600 --anchor "京圈太子"
```

**擷取層（best-effort）**：彈幕分頁上方可開關「彈幕擷取」。開啟後，web_ui 背景會監看正在錄製的抖音主播，自動連上抖音 IM websocket 擷取聊天訊息寫入資料庫（不動 recorder、不需重打包）。

> ⚠️ 擷取層以純標準庫實作抖音 websocket + protobuf 解碼，**抖音協定/簽章會不定期改版**，擷取可能失效需調整——屬盡力而為。**查詢／儲存／UI／CLI 完全獨立且可靠**，即使擷取失效，既有彈幕仍可查。目前僅實作抖音（現有主播全為抖音）。

---

## 錄影回放（依日期連續播放）

控制台「**錄影回放**」分頁：左側依日期列出錄影，選一天後中間播放器會**依時間順序連續播放**當天所有片段，一段播完自動接下一段（可關）。右側是當天清單可點選跳段，有彈幕字幕的片段標 💬，播放時可切換是否顯示（SRT 會即時轉成 WebVTT 由 `<video>` 原生字幕軌顯示）。

技術重點：

- 影片經 `/api/library/video/<id>` 串流，用 **HTTP Range**（`send_file(conditional=True)`），可拖曳快轉、無縫接續。
- clip id 是路徑的 SHA1（前 16 碼），伺服器端反查真實檔案，**不接受用戶端傳入路徑**；再加**目錄越界防護**，只允許保存路徑內的檔案。
- 檔案解析會自動對應**壓縮後的 `_hevc.mp4`** 或 **.ts→.mp4 轉檔**（DB 存的路徑可能過期），找不到檔案的片段自動略過。

---

## 錄製設定頁（畫質/路徑/格式/Cookie）

側邊欄「**設定 → 錄製設定**」可直接在控制台改 `config.ini`，不用手動編輯檔案：

- 畫質、影片格式（下拉選單，選項來自 config.ini 目前支援的值；若你的目前值不在常見選項裡，會多列一項保留原值）
- 保存路徑（純文字輸入——瀏覽器無法跳出系統原生資料夾選擇窗，請直接貼完整路徑）
- 分資料夾規則（依作者/時間/標題）、檔名是否含標題
- 循環時間（秒）、分段錄製開關 + 分段秒數
- Cookie（收合區塊，依 `config.ini` 的 `[Cookie]` 區段動態列出全部平台鍵，只能覆寫既有鍵不會新增）

存檔只改動你送出的欄位那一行文字，**保留 config.ini 原有的註解與排版**；儲存後會標示「⚠ 部分設定需重啟錄製器（DouyinLiveRecorder.exe）才會套用」——套用方式同其他 main.py 相關改動，跑 `build_main_exe.bat` 或直接重啟 `DouyinLiveRecorder.exe`。

---

## 統計頁（錄製時長排行/明細）

側邊欄「內容」分類下的「**統計**」：

- 篩選「全部歷史 / 本月 / 今日」，看排行榜（依累計時長排序）與摘要（總時長、總場次、平均場次時長、最長場次、今日/本月時長）
- 點排行榜某個主播展開該主播的單場明細（每場開始/結束時間、時長、畫質、結束原因）
- 「匯出 CSV」直接下載，欄位/順序與 `query_duration.py --export` 一致

等同於瀏覽器版的 `query_duration.py`，資料來源同一份 `recording_history.db`，CLI 與網頁版可交叉核對。

---

## GitHub 自動更新

啟動 web_ui 時會啟動背景檢查緒，每 6 小時：
1. 查 `https://api.github.com/repos/ihmily/DouyinLiveRecorder/commits/main`
2. 若 SHA 變了 → 下載最新 `src/spider.py` → 覆蓋（舊版備份成 `spider.py.upstream-bak`）
3. 寫進 `config/update_state.json`

**只自動更新 `spider.py`**（修平台 API 改版用），其他檔案（main.py 等）有我們的 hooks，不自動覆蓋。Web UI 頂部會顯示「有更新可用」徽章供手動套用。

---

## CLI 工具

```cmd
python query_duration.py --top 20                  排行榜
python query_duration.py --anchor "京圈太子"      單一主播明細
python query_duration.py --today                   只看今天
python query_duration.py --month 2026-05           看某月
python query_duration.py --export logs/r.csv       匯出 CSV
```

```cmd
python tools/find_duplicate_sessions.py "F:\main\record未分類\抖音直播\某主播資料夾"
                                                     掃描某主播資料夾，列出疑似「同一場直播錄兩次」
                                                     的 session 配對（開始時間差 <90 秒、重疊分段檔案
                                                     大小幾乎相同）。純唯讀，不搬不刪，僅列清單供你
                                                     確認後手動處理。
```

---

## 常見故障排查

**Web UI 顯示 `—`**
→ web_ui.py 沒跑或舊版。執行 `start_console.bat`（會 kill 舊的 + 重啟 recorder + web_ui + 自動開瀏覽器）。瀏覽器 Ctrl+Shift+R。

**（舊版）Tray icon 沒出現 / 浮動視窗顯示 Not Found**
→ 桌面小工具（widget）已棄用，功能已完全併入瀏覽器控制台，`install_autostart.bat` 也不再安裝它。若你仍手動執行 `start_widget.bat` 想用舊版浮動視窗：tray icon 被 Windows 藏在「^」溢位區可拖出來；`/widget` 路由需要 web_ui 沒有太舊。一般情況下改用 `start_console.bat` 即可，不需要理會這兩個問題。

**錄製某個直播間時 `list index out of range`**
→ 平台 API 改了，等 GitHub 自動更新或手動點 savebar 右側「有更新可用」徽章。

**「自動恢復(分)」欄位清不掉 / 編輯留空後仍是舊值**
→ 已修復。舊版前端把「留空」送成 `null`，後端當成「不變更」導致清不掉；現在留空或填 0 = 直接清除（恢復預設：隔天 00:00 重置）。
　若仍看到舊值：主播清單工具列點「**清空自動恢復時間**」一鍵清空全部，或編輯該主播把欄位清空後儲存即可；清掉後欄位顯示「—」。
　（設定存在 recorder 同層的 `config/streamer_limits.json`，通常是 `F:\main\DouyinLiveRecorder_v4.0.7\config\streamer_limits.json`，**不是** source 子資料夾那個。）

**健康狀態報「偵測到可能損壞的錄製」但其實錄得好好的**
→ 多半是**誤報已修復**前的舊行為：那些錄很久、正常結束的場次只是檔案已上傳網盤/移走，健康檢查舊版把「檔案不存在」一律當損壞。現版本會判定為已歸檔、不再報警（見上方「系統健康監測」）。重啟 web_ui 後即消失。

**改了 web_ui.py / 前端但畫面沒變**
→ 跑的是舊 web_ui 行程。執行 `start_console.bat` 重啟，再 Ctrl+Shift+R 強制重新整理瀏覽器。

**同一場直播被錄了兩次（同一資料夾、開始時間差 10~70 秒、`_000`~`_0xx` 分段檔案大小幾乎一樣）**
→ 已修復（2026-09-24）。根因：`taskkill /f /im DouyinLiveRecorder.exe` 沒加 `/T`，按「↻ 立即檢查開播」或重新部署時只殺掉主程式，正在錄的 ffmpeg 變成孤兒繼續錄，新啟動的 exe 不知道孤兒的存在，同一主播若剛好在直播就又開一個全新錄製。現在 `taskkill` 全部加了 `/T`（連子行程一起殺），`main.py` 也加了「已在錄製中就跳過」的防呆。**這次修復前已經錄下的重複檔案不會自動清掉**——用 `python tools/find_duplicate_sessions.py "<主播資料夾>"` 掃描列出疑似重複的 session 配對（唯讀，不搬不刪），確認後自行手動刪除其中一份。

---

## 上游

- License: 見 `LICENSE`
- 上游 README: https://github.com/ihmily/DouyinLiveRecorder
- v4.0.7 release: https://github.com/ihmily/DouyinLiveRecorder/releases/tag/v4.0.7
