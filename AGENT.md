# AGENT.md — 開發／維護指南（DouyinLiveRecorder 自訂版）

本檔是這個專案的**權威工程說明**，給任何要修改此專案的人或 AI agent 看。
使用者導向的操作說明在 `README.md`；改進路線在 `ROADMAP.md`；階段性進度在 `PROJECT_STATUS.md`。

---

## 1. 這是什麼

基於上游 [ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) v4.0.7 的**個人自用**多平台直播錄製工具，加上一層自訂功能：時長追蹤、Web 控制台、健康監測、磁碟管理、H.265 壓縮、彈幕擷取/查詢/轉字幕、依日期回放 + 回放標記、Discord 錄製通知、Discord 控制頻道（新增/移除主播）。

目標使用者：**單機、常駐錄製、有技術背景的個人**。不是 SaaS、不做多人/雲端（見 ROADMAP「明確不做」）。

---

## 2. 最重要的架構觀念：兩個執行體

這個專案有**兩個獨立執行的部分**，改東西前必須先分清楚你動的是哪一個：

| 執行體 | 是什麼 | 程式碼 | 改動如何生效 |
|--------|--------|--------|--------------|
| **Recorder** | `DouyinLiveRecorder.exe` | `main.py` + `src/spider.py` `src/stream.py` `src/duration_tracker.py` 等 | **必須用 `build_main_exe.bat` 重新打包 exe** 才生效（PyInstaller 把 main.py 編進 exe） |
| **Web UI** | Flask 控制台，跑原始碼 | `web_ui.py` + `src/*`（除上述 recorder 專用者）+ `web_static/` | **改完重啟 web_ui 即生效**，不用打包 |

> ⚠️ **最常見的坑**：改了 `main.py`（例如「特別關注」輪詢邏輯）卻沒重建 exe，會以為沒生效。凡是動到 `main.py` 或 recorder 用的 `src` 模組，一定要 `build_main_exe.bat` → 部署 → 重啟。Web UI 端的功能（下面第 4 節「Web UI 側模組」）則不需要。

兩者透過**共享檔案**溝通：`config\URL_config.ini`（主播清單）、`config\recording_history.db`（時長 SQLite，recorder 寫、web_ui 讀）、`config\streamer_limits.json`（上限/備註/特別關注旗標）。

---

## 3. 執行階段檔案位置（重要）

Recorder 與 web_ui 用「**exe/腳本所在資料夾**」往上一層找 `config\`。實務上：

- **安裝目錄（runtime）**：`F:\main\DouyinLiveRecorder_v4.0.7\`
  - `DouyinLiveRecorder.exe`、`_internal\`、`config\`、錄影輸出資料夾
- **原始碼目錄**：`F:\main\DouyinLiveRecorder_v4.0.7\source_with_duration_tracker\`
  - 所有 `.py`、`web_static\`、`.bat`、`pyembed\`（可攜 Python）、`dist\`（打包輸出）

**所有執行階段設定檔都在安裝目錄的 `config\`**，不是 source 子資料夾那個。清資料/改設定認明路徑：

```
config/URL_config.ini        主播清單（# 開頭 = 停用）
config/config.ini            錄製設定 + cookies（中文鍵、值用 是/否）
config/recording_history.db  時長 SQLite（recording_sessions 表）
config/streamer_limits.json  每主播 max_session_minutes / cooldown_minutes / note / priority
config/alerts.json           健康警報 + 磁碟自動管理策略（disk 區段）
config/compress.json         壓縮設定（含 enabled_encoders）
config/compress_state.json   壓縮統計 + 跳過清單
config/danmaku.db            彈幕 SQLite
config/rec_notify.json       Discord 錄製通知設定
config/webui_port.txt        web_ui 實際綁定的 port
config/update_state.json     GitHub 更新檢查狀態
```

---

## 4. 模組地圖

**Recorder 專用（改動需重建 exe）**
- `main.py` — 上游錄製主迴圈 + duration tracker hooks + 特別關注輪詢（`is_priority_url` / `PRIORITY_POLL_SECONDS`）
- `src/spider.py` `src/stream.py` `src/room.py` `src/ab_sign.py` `src/proxy.py` `src/utils.py` `src/logger.py` `src/initializer.py` — 上游抓流/簽名/工具（spider.py 由 GitHub 自動更新）
- `src/duration_tracker.py` — 時長 SQLite（recorder 寫入，web_ui 也讀）

**Web UI 側（改動只需重啟 web_ui）**
- `web_ui.py` — Flask 後端，~42 個 API 端點 + 背景 watchdog（每 60s）
- `src/health.py` — 健康評估（磁碟/錄製器/DB/壞檔/cookie），**純邏輯**
- `src/alerts.py` — 健康警報派送（ntfy/TG/webhook）+ 節流/恢復
- `src/disk_manager.py` — 磁碟暫停/清理規劃，**純邏輯**
- `src/netutil.py` — port 偵測 + webui_port.txt
- `src/compressor.py` — H.265 多編碼器並行壓縮（nvenc/qsv/amf/libx265）
- `src/danmaku_store.py` — 彈幕 SQLite 儲存/查詢，**純邏輯**
- `src/danmaku_capture.py` — 抖音彈幕 websocket 擷取（best-effort，純標準庫）
- `src/danmaku_subtitle.py` — 彈幕→ASS/SRT 字幕，**純邏輯**
- `src/library.py` — 錄影庫日期分組 + 檔案解析 + 播放清單，**純邏輯**
- `src/marks_store.py` — 回放標記（時間點/時間段 + 備註）SQLite 儲存，**純邏輯**（2026-08-31 新增）
- `src/rec_notify.py` — Discord 錄製通知（開始/結束、embed 卡片、批次合併）
- `src/discord_bot.py` — Discord 控制頻道機器人：在指定頻道用文字指令新增/移除監控中的主播（唯一使用 `discord.py` 第三方依賴的模組，2026-08-31 新增）
- `src/update_checker.py` — GitHub 自動更新（只更新 spider.py）
- `web_static/index.html` — 控制台單頁（分頁：儀表板/主播清單/彈幕查詢/錄影回放）
- `web_static/widget.html` — 浮動視窗精簡版

**CLI**
- `query_duration.py` — 時長查詢
- `query_danmaku.py` — 彈幕查詢 + `--subtitle` 產字幕

**啟動 / 部署腳本**
- `start_console.bat` — **建議日常用**：recorder + web_ui + 自動開瀏覽器，跳過 widget
- `start_widget.bat` — 完整版（含托盤 widget，widget 在某些機器會退出）
- `start_webui_silent.bat` — 只起 web_ui（給 autostart 用）
- `build_main_exe.bat` — PyInstaller 打包 exe → `dist\`
- `deploy.bat` — 停舊程序 → 備份 config → 覆蓋 exe+_internal → 重啟
- `install_all.bat` — 一鍵安裝（下載 pyembed + 裝依賴 + 打包 + 部署 + 啟動）
- `install_autostart.bat` / `uninstall_autostart.bat` — 開機自啟（登入時，recorder + 隱藏 web_ui）

---

## 5. 設計慣例（延續這些）

1. **純邏輯與 IO 分離** — 判斷/計算放在無副作用、可單元測試的函式（`health` / `disk_manager` / `danmaku_store` / `danmaku_subtitle` / `library` 都是這樣）；網路/檔案/破壞性動作留在薄薄的 web 層或明確的執行函式。
2. **預設安全** — 新功能預設**關閉**或不注入預設值；破壞性操作（磁碟清理、刪原檔）需明確開啟且可預覽。
3. **不靜默失敗** — 出錯要嘛推播、要嘛在 UI 標紅、要嘛回傳具體錯誤字串（例：Discord 測試按鈕會顯示 `HTTP 403` 這種真正原因，而非「失敗」）。
4. **設定檔用 JSON + `_deep_merge` 合併預設** — 見 `alerts.py` / `rec_notify.py` / `compressor.py` 的 `load_settings`/`save_settings` 模式，新增欄位不會破壞舊檔。
5. **不加新的第三方依賴** — 能用標準庫就用（彈幕 websocket、Discord webhook 都是純 `urllib`/`socket`/`ssl` 手寫），因為 recorder 用的是精簡可攜 Python。**唯一的例外是 `discord.py`**（`src/discord_bot.py`，Discord 控制頻道新增/移除主播，2026-08-31）：這個功能需要常駐監聽 Discord 頻道訊息，手刻 Gateway 協定的成本明顯過高，使用者已明確同意破例；`start_console.bat` 會自動用 `pyembed\python.exe -m pip install` 裝好，不需要使用者手動處理。之後若還有類似「需要常駐監聽某個外部服務」的需求，比照這次的做法先跟使用者確認是否要破例，不要預設可以用。
6. **每個新功能寫單元測試** — 針對純邏輯函式；大型檔案（web_ui/main）用「抽純邏輯 + 獨立片段 AST 驗證」補足。
7. **同一列的多個區塊，高度要封頂、不能讓內容把版面撐高** — 並排的區塊（例：回放頁的主播／日期／播放器／彈幕）用共同的高度基準（例如 `height:70vh`）+ `display:flex; flex-direction:column`，內部真正會變長的清單/表格用 `flex:1; min-height:0; overflow:auto`（而不是 `max-height`），資料一多就自己出現 scrollbar，不會撐高整個區塊、也不會把旁邊的區塊一起往下擠。日期／清單這種要「上下對半分」的，兩塊各給 `flex:1 1 0`。實例見 `web_static/index.html` 的 `#pl-anchors`／`#pl-dates`／`#pl-clips`／`#pl-dm-col`（`.mt-scroll` 的 CSS override）。

---

## 6. 開發工作流

**改 Web UI 側功能**（web_ui.py / 非 recorder 的 src / web_static）：
1. 改碼 → `python -m py_compile <檔>` 確認語法
2. 對純邏輯函式寫/跑單元測試
3. 重啟 web_ui：`start_console.bat` → 瀏覽器 Ctrl+Shift+R

**改 Recorder 側功能**（main.py / recorder 的 src）：
1. 改碼 → `py_compile`
2. `build_main_exe.bat`（在 Windows 上；PyInstaller 不能跨平台）
3. `deploy.bat`（停舊 → 備份 config → 覆蓋 → 重啟）
4. 驗證：主控台看倒數秒數 / 行為

**新增 API 端點**：加在 `web_ui.py`，前綴 `/api/`；回傳 `jsonify`；破壞性動作要有 dry-run/預覽或明確確認。

**新增設定頁欄位**：HTML 加元件（給唯一 id）→ JS 的 load/payload 函式各加一行 → 後端端點接收。改完檢查 HTML id 與 JS 參照數量一致。

---

## 7. 已知陷阱（踩過的雷）

- **exe 未重建**：改 `main.py` 沒 `build_main_exe.bat` → 功能看似無效。最常見。
- **`.bat` 編碼**：Windows 繁中 cmd 用 Big5 讀 .bat，檔案存 UTF-8 時中文字位元組會被誤判成 `|`/`&` 指令分隔符，整行 echo 被切斷報 `'xxx' is not recognized`。**部署/工具腳本一律寫純 ASCII**（`deploy.bat`、`start_console.bat` 就是這樣）。
- **沙箱掛載截斷**：（僅限用遠端 sandbox 開發時）大型檔案（web_ui.py/main.py/compressor.py）的掛載副本偶爾被截斷，`py_compile` 會誤報語法錯。用「複製到 /tmp 再 compile」或「讀實際檔案逐段確認 + 抽片段 AST 驗證」判斷。新檔（Write 建立）不受影響。
- **URL 比對要正規化**：recorder 的 `record_url` 會被正規化（補 https、去 query），與 `streamer_limits.json` 的 key 精確比對可能不符 → 用 `main.py` 的 `is_priority_url()`（`_normalize_url`：去 scheme/query/尾斜線、host 小寫）。
- **Discord 需要 User-Agent**：Discord 前置 Cloudflare 對無 UA 的請求回 `HTTP 403 error code:1010`。所有對 Discord 的 `urllib` 請求都要帶 UA（見 `rec_notify.py`）。
- **HTML `<video>` 字幕只吃 WebVTT**：SRT 要即時轉 VTT（`web_ui.py` 的 `_srt_to_vtt`）才能給 `<track>` 用。
- **`recording_sessions.file_path` 不能直接當真實檔案路徑用**：分段錄製時存的是 ffmpeg 樣板（如 `..._%03d.ts`），且原始 `.ts` 常在轉檔成 `.mp4`/`_hevc.mp4` 後被刪除。任何讀這個欄位的地方都要過 `_library.resolve_video()`（或要列出全部分段用 `resolve_all_segments()`），不能直接 `Path(file_path).exists()`。這個坑出現過兩次：回放頁清單（已修，見下方分段錄製記錄）、彈幕轉字幕的錄影選單 `/api/danmaku/sessions`（同樣改用 `resolve_video()`，並讓前端優先送 `resolved_path` 給 `/api/danmaku/subtitle`）。之後新增任何用到 `file_path` 的功能都要記得這件事。
- **彈幕內容解碼欄位編號 bug（已修）**：`danmaku_capture.py` 的 protobuf 手寫 reader 原本把 `ChatMessage.content` 猜成 field 5、`ChatMessage.user` 猜成 field 3——實際正確編號是 `content=field3`、`user=field2`（`User.nickName` 仍是 field 3，這層是對的）。更嚴重的是原本有個 fallback：`Response.messagesList`（field 1）走訪不到任何訊息時，會把整個 `Response` bytes 硬塞給 `_extract_chat` 當成單一 ChatMessage 解——而 `Response.internalExt`（一段 `internal_src:pushserver|first_req_ms:...|seq:...|wss_msg_type:...|wrds_v:...` 的內部診斷字串）剛好就是 field 5，跟舊版誤判的「content=field5」直接撞號，於是把這段診斷字串當彈幕內容存進 DB。已修正欄位編號、拿掉這個 fallback（結構對不上就回傳空清單，不再硬猜），並加一層防呆正則擋掉長得像這種診斷字串的內容。已用手寫 protobuf encode 重現舊 bug 並驗證新版修復（4 組案例：正常訊息/純 internalExt/非聊天訊息類型/防呆正則）。**修復前已經寫進資料庫的舊亂碼**不會自動消失，去「彈幕」分頁按「清除亂碼彈幕」（`POST /api/danmaku/purge_garbage`，比對 `internal_src:pushserver` 子字串刪除，不影響其他資料）。
- **彈幕 websocket 簽名：不是 a_bogus，是獨立的 MD5+JS 演算法（重要更正）**：實測抓到 `ws handshake failed: HTTP/1.1 200 OK ... Server: volc-dcdn`——代表根本沒真的升級成 websocket，落在 CDN 邊緣節點被擋掉，因為缺 `signature` 參數。**最初誤以為 websocket 的 signature 跟 `spider.py` HTTP API 用的 `ab_sign()`（a_bogus）是同一套**（`aid=6383` 剛好對得上，看起來合理），已實作並測試過這個版本，但參考真實可用的第三方專案 [`tomas41026175/DouyinLiveWebFetcher`](https://github.com/tomas41026175/DouyinLiveWebFetcher)（`liveMan.py`）後確認**這個假設是錯的**：a_bogus 是給 HTTP 房間進入/狀態 API 用的，webcast IM push websocket 的 `signature` 是完全不同的演算法——把固定順序的一組參數（`live_id,aid,version_code,webcast_sdk_version,room_id,sub_room_id,sub_channel_id,did_rule,user_unique_id,device_platform,device_type,ac,identity`）組成 `key=value,key=value,...` 字串、MD5 雜湊，再把 MD5 hex 丟進抖音官方一支未公開、常改版的大型 JS（`sign.js`，~56KB）裡呼叫 `get_sign(md5_hex)` 取得最終簽名。已改用這個正確流程：`danmaku_capture.py` 新增 `_generate_ws_signature(query)`，用專案已有的 `execjs`（`spider.py` 簽其他平台已在用，不算新依賴）執行 `src/javascript/douyin_danmaku_sign.js`；`_ws_url()` 改成完整比照參考專案的參數集（`screen_width`/`internal_ext`/`cursor`/`user_unique_id` 等），`run()` 的 host 也從 `webcast5-ws-web-lf.douyin.com` 改成參考專案驗證過的 `webcast100-ws-web-lq.douyin.com`。**`sign.js` 已放進 `src/javascript/douyin_danmaku_sign.js`**（原檔 ~474KB、高度混淆，來源 <https://github.com/tomas41026175/DouyinLiveWebFetcher/blob/main/sign.js>，用 execjs+node 載入並實測 `get_sign()` 可正常回傳簽名字串）。這個檔案不像 `ab_sign.py` 是可稽核的從頭實作，是整包原樣搬過來的第三方黑箱腳本，無官方文件、抖音改版時可能失效——若之後 handshake 又開始失敗，第一個該懷疑的就是這支腳本過期，需要回原專案重新抓最新版覆蓋。若 `execjs`/Node 環境不存在或這個檔案被移除，`_generate_ws_signature()` 會回傳 `None`，`_ws_url()` 直接送出不帶 signature 的 query（等同修正前的行為，預期會被 CDN 擋）。要確認是否真的生效：看「彈幕」分頁診斷灰字裡 workers 是不是變成「已連線」；若仍 handshake 失敗，貼一次新的錯誤訊息（尤其是 HTTP 狀態碼/回應內容或 `last_error`）方便繼續排查。
- **彈幕擷取是 best-effort，預設開啟**：`danmaku_capture._enabled` 預設 `True`，`web_ui.py` 啟動時自動跑協調器，不需要每次手動去「彈幕」分頁按「啟用擷取」。**沒有持久化**——這只是行程內的記憶體旗標，重啟 web_ui 就回到預設值（開啟）；用分頁的按鈕手動關掉的話，關掉的狀態撐不過下一次重啟。抖音 IM websocket/protobuf 協定會改版且無法在建置環境對真實抖音驗證；擷取失效不影響查詢/字幕/UI（那些完全獨立可靠）。
- **`resolve_video()`/`resolve_all_segments()` 找真實檔案很貴，記得傳 cache**（見 `PERF_PLAN.md` P0-1）：這兩個函式對分段錄製 session 要對多種副檔名/壓縮組合各跑一次 `glob.glob()`（真的磁碟掃描），`group_by_date()`/`build_playlist()` 對每個 session 都會呼叫一次，沒有 cache 的話「點主播」「進回放頁」都要重新掃一次該範圍全部 session 的磁碟——這是回放頁效能問題的根因。`web_ui.py` 已加 `_RESOLVE_VIDEO_CACHE`/`_RESOLVE_SEGMENTS_CACHE`（行程內 dict，重啟就清空）並在所有呼叫處傳入；**新增任何呼叫 `_library.resolve_video()`/`resolve_all_segments()` 的地方都要記得傳 `cache=` 參數**，不然那個呼叫點會繞過 cache、變回原本的效能問題。這兩個函式的 cache 是「驗證後才信任」：命中時先用注入的 `exists()`（單一 stat，不是重新 glob）確認快取路徑還在，不在才重新掃——所以不會有「檔案已經被轉檔/壓縮換掉，但還是回傳舊路徑」的風險，也不需要 recorder 端額外發送失效通知。`library.py` 的預設行為（不傳 `cache` 參數）完全不變，純函式單元測試不受影響。**已知的別的 bug（跟這次 cache 工作無關，測試 cache 時順便發現）**：`resolve_all_segments()` 的 `index_re`（抓 `..._%03d.ts` 樣板裡的分段編號）目前只認得「數字後面直接接副檔名」（如 `a_002.mp4`），如果那個分段已經被壓縮器改名成 `a_002_hevc.mp4`（數字跟副檔名中間多了 `_hevc`），這個正則就配不到，導致該分段完全不會被當成「找到的分段」列進清單——實務上代表：一個分段錄製的 session，如果裡面的分段已經被壓縮，回放頁很可能少顯示這些已壓縮的分段（或整段退化成只顯示 `resolve_video()` 找到的第一個分段）。這個獨立於本次效能優化，需要另外排查修正 `index_re` 的建構方式。
- **回放標記（marks）為什麼用 session_key + 絕對 epoch，不是 clip_id + 相對秒數**（2026-08-31 新增）：一開始想直接綁在 clip_id 上（跟 `/api/library/video/<clip_id>`、`/api/library/clip/<id>/danmaku` 一樣），但使用者需求後來加了「標記可以是時間段」「可以跨影片（限同一場分段錄製 session 的相鄰片段）」——clip_id 是單一片段的身分，沒辦法表達「橫跨兩個片段」，而且壓縮改檔名後 clip_id 會變、標記會跟丟。改成用 `recording_sessions.file_path`（未 resolve 前的原始樣板字串，整場 session 生命週期內不變）當 `session_key`，時間存絕對 unix epoch（`start_epoch`/可選 `end_epoch`），讀取時才用 `web_ui._session_clip_windows()` 重新算出目前每個片段的 epoch 區間，再用 `library.locate_epoch_in_windows()`（純函式，見 `tests/test_library.py` 的 `LocateEpochInWindowsTests`）換算成「屬於哪個目前的 clip_id、片段內第幾秒」。這個設計跟 danmaku 的同步邏輯（用 ts 對 start_epoch 算 offset，而不是把 offset 直接存死）是同一個道理，好處是壓縮改檔名不會讓已存的標記失效。**範圍刻意限制在同一 session 內的相鄰片段**（不含斷線重連產生的新 session）——使用者已確認這樣夠用，跨 session 判斷「前後接續」的複雜度不值得。新增/改動這段邏輯時要注意：`_resolve_clip_session()` 現在多回傳 `session_key`/`session_start_time`/`session_duration_sec` 三個欄位（原本的 `start_time`/`duration_sec` 在分段錄製時已經被覆寫成該片段自己的估計值，這三個新欄位才是整場 session 未覆寫的原始值），任何要重新算全部片段時間窗的地方都要用這三個新欄位，不能用被覆寫過的 `start_time`/`duration_sec`。
- **Discord 控制頻道機器人（`src/discord_bot.py`，2026-08-31 新增）**：純指令解析（`parse_command`/`normalize_target_url`/`find_matching_entries`/`format_list`/`format_help`）完全不依賴 `discord` 套件、有完整單元測試（`tests/test_discord_bot.py`）；連線/收發訊息才用 `discord.py`，且整段包在 `try: import discord except Exception: discord = None`，套件沒裝好只會讓這個 bot 不啟動（`is_available()` 回 False），不影響其他功能。**背景連線用一個 daemon thread 呼叫 `discord.Client.run(token)`**（這個呼叫本身阻塞，內部自己建 event loop，不跟 Flask 的同步 request 搶主執行緒）；新增/移除/清單三個 callback（`web_ui._discord_add_streamer`/`_discord_remove_streamer`/`_discord_list_streamers`）是一般同步檔案 IO，從 async handler 用 `run_in_executor` 丟到執行緒池執行，避免卡住 Gateway 心跳。**改設定後的重連是 best-effort、非同步的**：`PUT /api/discord_bot` 會呼叫 `stop()`（透過 `asyncio.run_coroutine_threadsafe` 排程舊連線自己關閉）等 0.5 秒再 `start()`，多數情況下夠新連線接手，但沒有嚴格保證——如果重連卡住，使用者重啟 web_ui 一定會生效；這段也包了 try/except，重連本身出錯不會讓設定存檔的請求整個回 500。**已修過的坑**：`discord.Client.loop` 在連線真正建立前不是 `None`，是 discord.py 自己的 `utils.MISSING` sentinel 物件——`stop()` 原本寫 `getattr(c, "loop", None) is not None` 來判斷「有沒有真的連線」，MISSING 通過這個判斷式（它不是 None）但沒有 `.is_closed()` 方法，一呼叫就 `AttributeError`，實測連續按兩次「儲存並重新連線」（或剛啟用馬上存檔）100% 觸發，因為第二次 `stop()` 抓到的是還沒跑到 `run()` 內部設定真正 event loop 那一步的舊 client。已改成 `isinstance(loop, asyncio.AbstractEventLoop)` 判斷。**這是專案唯一使用 `discord.py`（第三方依賴）的模組**，`start_console.bat` 已更新成連 `discord.py` 一起裝（見上面規則 5 的例外說明）。Discord Bot 需要在 Developer Portal 手動打開「MESSAGE CONTENT INTENT」才能讀到訊息文字內容，這是最容易漏掉的設定步驟。
- **`start_console.bat` 沒加 `-u`，print() 診斷曾經「看起來卡住」其實只是被緩衝**（2026-09-08 排查 Discord LoginFailure 時發現）：`"pyembed\python.exe" web_ui.py 1>"logs\web_ui.log" 2>&1` 沒帶 `-u`，CPython 的 stdout 只要被導向檔案/管線就會變成 block-buffered（預設約幾 KB 緩衝區才 flush 一次），但 `sys.stderr` 一定是無緩衝的——而 Flask/Werkzeug 的請求存取記錄剛好是印到 stderr，所以 log 檔裡 `GET/PUT ...` 那些行永遠即時出現，害人誤以為 log 是即時的，實際上同一時間點的 `print()`（例如 `discord_bot.py` 加的診斷訊息）可能安靜地卡在緩衝區裡好幾十秒甚至更久才真正寫進檔案，一度誤判成「背景執行緒真的卡住沒有在動」。已在 `start_console.bat`、`start_webui_silent.bat` 的 python 呼叫加上 `-u`（unbuffered）修正——**這是全域性的教訓**：之後任何要靠讀 `logs/web_ui.log` 的 `print()` 輸出來排查問題，都要先確認這兩支 bat 檔有沒有 `-u`，沒有的話 log 內容不能信任「目前為止就是全部發生過的事」。
- **`discord_bot.py` 的 `start()` 舊版有「殭屍執行緒卡死所有後續重連」的 bug（已修，2026-09-08）**：背景 thread 若卡在解析 DNS／建立 Gateway 連線（無 timeout）等步驟，`client.close()` 排程不一定能讓它真的結束，`_bot_thread.is_alive()` 會永遠回 True；而 `start()` 原本一看到 `is_alive()` 就直接 `return True` 不建立新執行緒，等於使用者不管改幾次 token/設定再存檔都不會真的生效，畫面永遠卡在「連線中」，只有完整重啟 web_ui 進程才能救回來。已改成：`stop()` 一律清空 `_bot_thread`/`_client` 全域參照（daemon thread，process 結束就會跟著死，放著不管沒關係），`start()` 每次呼叫遞增一個 `_generation` 編號、綁在該次建立的 `_ControlClient` 上，舊的殭屍執行緒之後才回呼（`on_ready`/例外處理）時比對編號不符就不再更動全域的 `_connected`/`_last_error`，避免蓋掉新連線已經成功的狀態。
- **斜線指令（`/add` `/remove` `/list` `/help`，2026-09-08 新增）**：用 `discord.app_commands.CommandTree`，指令名稱刻意用英文（中文指令名稱在 Discord API 理論上也合法，但英文對未來相容性更保險），內部直接組成跟文字指令一樣的 cmd dict 丟給既有的 `_dispatch()`，兩套介面共用同一份行為不重複維護。`on_ready` 裡會抓監控頻道所屬的 guild 做 `tree.sync(guild=...)`（guild-specific sync 立即生效；退化成全域 sync 的話 Discord 官方說可能要等最多 1 小時才會出現，所以優先用 guild sync）。**斜線指令需要邀請 bot 時多勾 `applications.commands` scope**，只有 `bot` scope 的舊邀請連結要重新走一次授權流程，否則 `tree.sync()` 會失敗或指令不出現在 `/` 選單。
- **`threading.Thread` 子類別絕對不能用 `self._stop` 當自訂屬性名**：`Thread` 內部有一個私有方法 `self._stop()`，在 thread 結束時（bootstrap 的 `finally`）以及每次 `is_alive()`/`join()` 呼叫 `_wait_for_tstate_lock()` 時都會被 Python 自己呼叫一次做內部狀態清理。`danmaku_capture.py` 的 `DouyinDanmakuClient` 原本寫 `self._stop = threading.Event()`（一個常見、直覺但錯誤的命名選擇），蓋掉了那個內建方法；只要底層 thread 已經結束（websocket 斷線幾乎必然如此），下一次協調器呼叫 `is_alive()` 就會撞成 `TypeError: 'Event' object is not callable`，而且這個例外會讓協調器整個 poll 迴圈中斷在該筆 URL，之後同一輪其他所有主播都不會被處理——等於整個彈幕收集被卡死到那個主播下播為止。已改名為 `self._stop_event`。**教訓：`threading.Thread` 子類別的自訂旗標/事件屬性一律加後綴避免撞名**（如 `_stop_event`、`_should_stop`），不要用 `_stop`、`_started`、`_target` 等 Thread 已使用的私有名稱。**排查「收不到彈幕」**：開「彈幕」分頁，狀態列旁的灰字診斷會顯示 db 檔案是否存在/筆數、協調器是否啟動、最近一次 websocket 斷線的 close code/原因（`GET /api/danmaku` 的 `db`/`capture.last_close`/`capture.last_error` 欄位）。最常見情況是連線建立後幾秒內就被關閉且訊息數 0——通常代表抖音要求的簽名參數（`_ws_url()` 目前沒帶 `signature`）已變嚴，需要對照官方網頁的即時封包更新簽名邏輯。

---

## 8. 測試

**`tests/` 目錄是真正持久化、會留在 repo 裡的回歸測試**（2026-08-30 起）：標準庫 `unittest`，不新增第三方依賴（沒用 pytest）。執行：

```
python -m unittest discover -s tests -v
```

涵蓋 `src/library.py`（含 P0-1 的 resolve cache「驗證後才信任」邏輯、回放標記用的 `locate_epoch_in_windows` 時間窗比對）、`src/danmaku_subtitle.py`（ASS/SRT builder）、`src/danmaku_store.py`（真的開一個 tempfile SQLite，不是 mock）、`src/danmaku_capture.py` 的純 protobuf 解碼邏輯（`_iter_fields`/`_decode_chat_messages`/`parse_push_frame`，用 `tests/_protobuf_helper.py` 手刻的 encoder 產生測試 fixture，跟被測模組一樣不依賴 protobuf 套件）、`src/health.py`（`evaluate_health` 的磁碟/DB/錄製器/webui 降級判斷、`classify_recording`/`evaluate_recordings` 的可疑錄製偵測、`looks_like_cookie_issue`/`evaluate_cookie_health` 的 cookie 過期偵測，2026-08-31 補）、`src/marks_store.py`（回放標記 SQLite 儲存，真的開 tempfile SQLite，2026-08-31 補）、`src/discord_bot.py`（純指令解析：`parse_command`/`normalize_target_url`/`find_matching_entries`/`format_list`/`format_help`/設定讀寫，不含實際 Discord 連線，2026-08-31 補）。這 188 個測試裡包含好幾個過去修過的具體 bug 的回歸樁：`_stop` 命名撞名的間接效應、彈幕 internalExt 誤判成 content、`%03d` 路徑解析、主播序号前綴分組、resolve cache 的失效邏輯。**新增/改動這幾個模組的純邏輯時，記得同步補測試、且跑一次 `python -m unittest discover -s tests -v` 確認沒有壞掉既有的。**

**已知的一個測試被標記 `@unittest.expectedFailure`**（`tests/test_library.py` 的 `test_compressed_twin_preferred_per_index`）：對應上面提到的「`resolve_all_segments()` 抓不到已壓縮分段檔」那個尚未修的 bug——這是刻意的，用來釘住「目前就是壞的、以後修好了這個測試會變成 unexpected success 提醒你拿掉這個裝飾器」，不是測試本身寫錯。

**`web_ui.py`/`main.py` 這類跟 Flask app context、全域狀態、真實 DB 連線、外部行程綁在一起的部分，目前沒有自動化測試**，仍然是舊慣例：改動後用臨時 script + `py_compile` + 手動跑 web_ui 驗證。這是刻意的取捨（這些模組要嘛需要重的 monkeypatch 才能孤立測試、要嘛本身就是薄薄一層膠水，投報率不如把心力放在 `tests/` 目錄涵蓋的純邏輯模組上），新增第三方依賴（如 pytest + Flask test client）能讓這塊更好測，但目前還沒有引入。`disk_manager`/`netutil`/`priority`/`compressor`/`rec_notify` 仍缺持久化測試（`health.py` 已於 2026-08-31 補上，其餘尚未排入）。

---

## 9. 文件分工

- **`README.md`** — 使用者操作說明（安裝、各功能怎麼用、疑難排解）
- **`AGENT.md`**（本檔）— 工程/架構/慣例，改碼前先讀
- **`CLAUDE.md`** — 指向本檔 + Claude Code 專用備註
- **`ROADMAP.md`** — 產品優先級與已完成/待辦
- **`PROJECT_STATUS.md`** — 階段性進度快照/交接

改功能時請**同步更新對應文件**（新功能 → README 教學 + ROADMAP 標完成 + PROJECT_STATUS 補一條）。
