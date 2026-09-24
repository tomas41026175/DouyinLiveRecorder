# 專案進度總覽 — DouyinLiveRecorder（自訂版）

> 本文件彙整近期開發進度，作為交接／回顧用。
> 最後更新：2026-08-26　｜　目標使用者：**個人自用**（常駐錄製、技術背景）
> 相關文件：`README.md`（使用說明）、`ROADMAP.md`（優先級路線圖）、`UIUX_SPEC.md` / `UIUX_TASKS.md`（2026-08 控制台重構規格與逐項驗收）

---

## 1. 一句話定位

常駐型、單機、個人用的多平台直播錄製工具。三層核心價值：**能錄到**（60+ 平台）、**錄得有紀錄**（SQLite 時長追蹤）、**管得動**（Web 控制台（側邊欄導覽，含設定/統計頁）+ 每主播上限；桌面 widget 已棄用）。

近期開發主軸：把產品從「壞了自己發現」推進到「**壞了會通知你、且會自我保護**」，並大幅降低儲存成本。

---

## 2. 本階段完成的功能（依主題）

### 2.1 系統健康監測 + 主動警報
- **`src/health.py`** — 純函式健康評估（無 IO，可單元測試）：磁碟 / 錄製器行程 / 資料庫 / 錄製完整性 / cookie 過期，輸出 ok / warn / critical + 問題清單。
- **`src/alerts.py`** — 複用既有推播管道（ntfy / Telegram / Webhook），惡化時通知、恢復時再通知；內建**防洗版**（同一問題 30 分鐘內只發一次、僅狀態轉變時觸發）。
- **watchdog 背景緒**（web_ui.py）每 60 秒評估一次；控制台頂部有**健康狀態膠囊**（綠/黃/紅 + 問題清單）與收合式**通知設定面板**（含一鍵測試）。
- 預設**關閉**，不填不影響既有行為。

### 2.2 穩定性批次（P0，全數完成）
| 項目 | 內容 | 檔案 |
|------|------|------|
| P0-1 錄製完整性驗證 | 揪出秒退無檔／檔案被截斷／error/crash 的壞檔；已正常結束後檔案被移走/上傳刪除的**不誤報** | `src/health.py` |
| P0-2 Cookie 過期偵測 | 啟發式辨識 401/403/登录/實名 類錯誤，指名哪個平台 | `src/health.py` |
| P0-3 磁碟自動管理 | 剩餘過低暫停新錄製（防抖）；選用的自動清理最舊錄影（預設關閉，可預覽） | `src/disk_manager.py` |
| P0-4 Port 避讓 | 8765 被占用自動試 8766–8770，寫入 `config/webui_port.txt`，widget 自動跟上 | `src/netutil.py` |
| P0-5 log 保留 | web_ui 主控台輸出輪替保留近 5 份 | `web_ui.py` |

### 2.3 特別關注（每分鐘檢測開播）
- 一般主播依 `config.ini` 循环时间（預設 300 秒）輪詢；標記「特別關注」的主播縮短為 **60 秒**。
- 旗標存 `streamer_limits.json` 的 `"priority": true`，recorder 依檔案 mtime 快取。
- 主播清單有「關注」欄星號可即時切換，編輯視窗也有勾選框。
- **⚠️ 重點**：此邏輯在 `main.py` 內，`main.py` 打包進 `DouyinLiveRecorder.exe`，所以**改動後必須重新 `build_main_exe.bat` 重建 exe 並部署**才會生效（web_ui 端的星號/欄位是即時的，但實際輪詢間隔由 exe 決定）。
- URL 比對已改為**正規化比對**（`is_priority_url()`：去 scheme/query/尾斜線、host 小寫），避免 recorder 正規化後的 URL 與 JSON key 精確字串不符而靜默失效。

### 2.9 錄製通知（Discord）
- **`src/rec_notify.py`** — 由 web_ui 的 60 秒 session 輪詢驅動：diff 前後兩次 `active_sessions()`，新出現＝開始錄製、消失＝結束錄製，發到 **Discord webhook**（或通用 webhook）。**不需重建 exe**（跑在 web_ui 原始碼端）。
- 啟動時 `prime()` 一次避免既有錄製全被當成「開始」洪水式通知；設定頁可開關「開始/結束」各自通知、一鍵測試。
- 設定存 `config/rec_notify.json`，預設關閉。設定頁在「⚙ 通知設定」面板新增「錄製通知（Discord）」區塊。

### 2.4 錄完自動壓縮（H.265）+ 多編碼器並行
- **`src/compressor.py`** — 背景掃描已錄完的檔案轉 H.265，體積約省 40–60%。
- **安全機制**：15 分靜置才視為錄完、先輸出 `.part` → 時長驗證一致 + 確實變小才改名刪原檔、跳過清單、低於正常 CPU 優先權、空間不足自動跳過。
- **多編碼器並行**（本階段重點）：自動偵測 `hevc_nvenc`(NVIDIA) / `hevc_qsv`(Intel) / `hevc_amf`(AMD) / `libx265`(CPU)，每個編碼器各拿**不同的檔案**同時壓，吞吐量倍增；同一檔絕不拆給多編碼器。可**手動勾選**目前要並行哪幾個。硬體編碼失敗自動退回 CPU。
- **UI**：總進度條 + 每條編碼線各一條進度條；偵測到的編碼器清單（✅啟用 ☑可用 ✖無）。

### 2.5 UI／資料修正
- 儀表板新增「硬碟剩餘空間」卡片。
- 主播清單新增「自動恢復(分)」欄位 + 工具列「清空自動恢復時間」按鈕。
- 修正 cooldown「幽靈 5 分」：`/api/streamers` 不再注入預設值，未設定回傳 `null` → 顯示「—」。
- 磁碟滿時控制台降級不噴 500、占用大小仍可檢視。

### 2.6 彈幕查詢（弹幕）+ 擷取
分兩層：**可靠核心**（100% 可測）與 **best-effort 擷取層**（需實測）。

- **`src/danmaku_store.py`** — SQLite 彈幕儲存 + 查詢（主播/URL/關鍵字/發言者/時間範圍、分頁、CSV 匯出、prune），純邏輯無網路。
- **控制台「彈幕查詢」分頁** — 主播下拉 + 關鍵字/發言者/日期篩選、分頁瀏覽、匯出、擷取開關。
- **`query_danmaku.py` CLI** — `--anchors / --anchor / --keyword / --user / --since / --export`。
- **`src/danmaku_capture.py`（best-effort）** — 純標準庫（socket/ssl/struct/gzip）實作抖音 IM websocket + 最小 protobuf 解碼；掛在 web_ui 背景監看正在錄製的抖音主播自動連線擷取，寫入資料庫。**不動 recorder、不需重打包**。
- **⚠️ 限制**：抖音彈幕協定/簽章會不定期改版且無法在建置環境對真實抖音驗證，擷取屬盡力而為、可能需微調（最可能是 websocket `signature` 參數）。**查詢/儲存/UI/CLI 完全獨立可靠**，擷取失效不影響既有彈幕查詢。目前僅實作抖音。
- 資料存 `config/danmaku.db`。

### 2.7 彈幕轉字幕（旁載 .ass + .srt）
- **`src/danmaku_subtitle.py`** — 純轉換：彈幕時間戳 − 錄影開始時間＝影片中出現秒數。產出兩種：
  - **ASS 滾動彈幕** — 多行右→左飛過、依 lane 分配避免重疊，像原生彈幕（需 PotPlayer/mpv/VLC）。
  - **SRT 底部字幕** — 同一短窗內多則併成一格、可選含發言者名稱。
  - 超出影片 [0, duration] 範圍的彈幕自動略過；毫秒/百分秒進位皆處理。
- **旁載輸出**（不動原影片）：`<影片名>.danmaku.ass` / `.srt`，播放器自動載入。
- 控制台彈幕分頁「彈幕轉字幕」選錄影一鍵產生；CLI `query_danmaku.py --subtitle <影片> --start <ISO> --duration <秒>`。

### 2.8 錄影回放（依日期連續播放）
- **`src/library.py`** — 純邏輯：依本地日期分組已完成錄影、解析真實檔案（對應壓縮後 `_hevc.mp4` / `.ts→.mp4` 轉檔 / 缺檔略過）、建當天播放清單（時間排序、偵測旁載字幕）、目錄越界防護。
- **控制台「錄影回放」分頁** — 左側日期清單、中間 `<video>` 播放器（播完自動接下一段、prev/next、字幕開關）、右側當天片段清單可跳段。
- **串流端點**：`/api/library/video/<id>` 用 `send_file(conditional=True)` 支援 **HTTP Range**（可拖曳、無縫接續）；`/api/library/subtitle/<id>` 將 SRT 即時轉 **WebVTT** 供 `<video><track>` 用。
- **安全**：clip id = 路徑 SHA1，伺服器反查真實檔案，**不接受用戶端路徑** + 目錄越界防護。

### 2.9 回放標記（時間點/時間段 + 備註，2026-08-31）
- **`src/marks_store.py`** — 純邏輯 SQLite 儲存，鍵為 `session_key`（recording_sessions.file_path 原始值）+ 絕對 epoch（`start_epoch`/可選 `end_epoch`），不是 clip_id + 相對秒數——這樣標記可以表達「跨同一場分段錄製 session 的相鄰片段」的時間段，且壓縮改檔名不會讓已存的標記失效（設計理由/取捨見 `AGENT.md` §7）。
- **`GET/POST /api/library/clip/<id>/marks`、`DELETE /api/library/marks/<id>`** — 讀取時用 `web_ui._session_clip_windows()` + `library.locate_epoch_in_windows()`（純函式）把絕對 epoch 換算回「目前屬於哪個 clip、片段內第幾秒」；新增時把使用者輸入的（片段內）秒數換算成絕對 epoch 才存。
- **回放頁 UI** — 播放器下方「&#128204; 加標記」按鈕，點擊直接帶入目前播放時間，勾選「加入結束時間」可標一段區間；標記清單依時間排序，點擊直接跳轉（同一天清單內的片段直接切換，若指向清單外的片段則直接改 `<video>` src 播放，防呆但不同步在清單上高亮）；每則標記可刪除。

### 2.12 新增主播支援抖音號、Discord 控制頻道（2026-08-31）
- **控制台「新增主播」欄位** — placeholder 更新提示可以直接貼 `https://live.douyin.com/抖音號` 這種格式（沿用既有 URL 輸入，不用額外解析）。
- **`src/discord_bot.py`（新）** — 在指定 Discord 頻道用文字指令新增/移除監控中的主播：`!新增 <網址或抖音號> [主播名稱]`、`!移除 <主播名稱或網址片段>`、`!清單`、`!help`。**這是專案唯一破例引入的第三方依賴 `discord.py`**（需要常駐監聽頻道訊息，手刻 Discord Gateway 協定成本過高，使用者已同意破例，見 `AGENT.md` §2 規則 5）；純指令解析（`parse_command`/`normalize_target_url`/`find_matching_entries`/`format_list`/`format_help`）不依賴 `discord` 套件、完整單元測試，連線層才用 `discord.py`，套件沒裝好只會讓 bot 不啟動、不影響其他功能。
- **`GET/PUT /api/discord_bot`** — 設定頁新增「Discord 控制頻道」面板（token/頻道 ID/指令前綴 + 啟用開關 + 連線狀態），儲存後自動斷線重連（best-effort）。
- **`start_console.bat`** 已更新成連 `discord.py` 一起自動安裝，使用者不用手動 pip install。

### 2.10 控制台 UI/UX 重構（2026-08，含 P1-1/P1-2 + 技術債清理）
把控制台從「兩個分頁 + 彈出式『⚙ 通知設定』面板」重做成側邊欄導覽的完整主控台，分五階段完成（規格 `UIUX_SPEC.md`，逐項驗收 `UIUX_TASKS.md`），**全程等價/擴充重構，未破壞既有功能**：

- **階段 0-1**：design tokens + 共用元件 class + 側邊欄（監控/內容/設定三組）+ topbar + hash 路由；舊 4 頁內容整段搬進新框架（單一 DOM，無重複 id）。
- **階段 2**：儀表板拆成 KPI / 系統健康 / 磁碟 / 正在錄製 / 壓縮佇列多個 panel；topbar 健康燈/port/刷新 + 側邊欄底部迷你狀態全站共用同一份輪詢。
- **階段 3**：`GET /api/library/clip/<id>/danmaku` 新端點；回放頁新增第 4 欄同步彈幕（跟播捲動/高亮/點擊跳轉/關鍵字搜尋/可收合），與既有 VTT 字幕並存。
- **階段 4（P1-1、P1-2）**：
  - **P1-1 設定 GUI** — `GET/PUT /api/config` + 「設定 → 錄製設定」頁：畫質/保存路徑/分資料夾/格式/循環/分段/Cookie 可視化編輯，逐行 regex 替換寫回、保留 config.ini 原有註解與排版（不用 configparser）。
  - **P1-2 統計頁** — `GET /api/stats/overview|anchor|export` + 「統計」頁：排行榜/摘要/單主播明細/CSV 匯出，SQL 邏輯對照 `query_duration.py`。
  - 額外：`GET /api/compress/overview`（60s 快取）壓縮占比 panel + 獨立於自動開關的手動「立即開始壓縮」（修掉舊版「關閉自動壓縮後手動掃描其實無效」的行為缺口）。
- **階段 5（技術債清理）**：新增 `src/common.py`，收斂 `alerts.py`/`compressor.py`/`rec_notify.py` 三份重複的 `deep_merge`/JSON 設定讀寫/`http_post_json`（含 UA、代理三態語意差異保留），以及 `duration_tracker.py`/`query_duration.py`/`web_ui.py` 重複的秒數→`H:MM:SS`格式化；移除確認零呼叫端的死碼（`rec_notify.py._now_hm`/單則 `format_start`/`format_stop`/`build_start_embed`/`build_stop_embed`、`health.py.diff_issue_codes`）與多處未用 import（`alerts.py`/`rec_notify.py`/`duration_tracker.py`/`web_ui.py`）；桌面 widget 全面標記棄用（README 註明、確認 `install_autostart.bat` 未安裝），不刪檔，是否刪除留給使用者決定。

**驗證**：每個抽出的共用函式都寫了等價性單元測試（deep_merge、JSON 讀寫 round-trip 含損毀/非 dict fallback、http_post_json 的三態 proxy 分支、fmt_duration 對照 `str(timedelta(...))`），以及 `alerts.py`/`compressor.py`/`rec_notify.py` 委派後的行為測試（`_post_json` 回傳型別不變、Discord UA/proxy 仍正確傳遞）；所有改動檔案皆複製到獨立環境 `py_compile` + `pyflakes` 確認無編譯錯誤與新增的未用 import。

---

## 3. 檔案異動清單

**新增模組（`src/`）**
```
health.py         健康評估（純邏輯）           ~10.7 KB
alerts.py         主動警報派送 + 節流/恢復      ~8.3 KB
disk_manager.py   磁碟暫停/清理規劃（純邏輯）   ~6.0 KB
netutil.py        port 偵測 + webui_port.txt   ~1.6 KB
compressor.py     多編碼器並行壓縮             ~29.3 KB
danmaku_store.py  彈幕 SQLite 儲存 + 查詢（純邏輯）
danmaku_capture.py 抖音彈幕 websocket 擷取（best-effort，純標準庫）
danmaku_subtitle.py 彈幕→字幕（ASS 滾動 + SRT 底部，純邏輯）
library.py        錄影庫：日期分組 + 檔案解析 + 播放清單（純邏輯）
rec_notify.py     錄製開始/結束通知（Discord/webhook，poll-driven）
```

**新增 CLI（專案根目錄）**
```
query_danmaku.py  彈幕查詢 CLI（--anchors/--anchor/--keyword/--user/--since/--export）
```

**主要修改**
```
web_ui.py         API 端點總計 32；watchdog / port / log / 壓縮 / 磁碟 / 彈幕端點 + 彈幕協調器
main.py           特別關注：get_priority_urls() + 60 秒輪詢覆寫
web_static/index.html   健康膠囊、通知面板、磁碟管理、壓縮多線 UI、關注欄、剩餘空間卡、彈幕查詢分頁
widget.py / widget_window.py   讀 webui_port.txt 對應實際 port
README.md         新增健康監測／磁碟管理／特別關注／並行壓縮／彈幕查詢章節
ROADMAP.md        P0 標記完成，重排後續建議
```

**設定檔（執行階段自動產生，皆在 recorder 同層 `config/`）**
```
alerts.json          健康警報設定 + 磁碟自動管理策略（disk 區段）
compress.json        壓縮設定（含 enabled_encoders 手動清單）
compress_state.json  壓縮統計 + 跳過清單
webui_port.txt       web_ui 實際綁定的 port
streamer_limits.json 每主播上限 / 備註 / cooldown / priority
danmaku.db           彈幕資料庫（SQLite）
```

---

## 4. API 端點總覽（web_ui.py）

**主播 / 狀態**：`GET /api/status`、`GET|POST /api/streamers`、`PUT|DELETE /api/streamers/<n>`、`POST /api/streamers/<n>/toggle`、`POST /api/streamers/batch`、`POST /api/streamers/priority`、`POST /api/stop`
**上限**：`GET|PUT /api/limits`、`POST /api/limits/clear_cooldown`
**健康 / 警報**：`GET /api/health`、`GET|PUT /api/alerts`、`POST /api/alerts/test`
**磁碟**：`GET|PUT /api/disk/policy`、`POST /api/disk/cleanup`
**壓縮**：`GET|PUT /api/compress`、`POST /api/compress/scan`、`POST /api/compress/pause`
**彈幕**：`GET /api/danmaku`、`GET /api/danmaku/anchors`、`POST /api/danmaku/capture`、`GET /api/danmaku/export`、`GET /api/danmaku/sessions`、`POST /api/danmaku/subtitle`
**錄影回放**：`GET /api/library/dates`、`GET /api/library/day`、`GET /api/library/video/<id>`（Range）、`GET /api/library/subtitle/<id>`（VTT）
**回放標記**：`GET|POST /api/library/clip/<id>/marks`、`DELETE /api/library/marks/<id>`
**Discord 控制頻道**：`GET|PUT /api/discord_bot`
**錄製通知**：`GET|PUT /api/rec_notify`、`POST /api/rec_notify/test`
**更新 / 其他**：`GET /api/update/status`、`POST /api/update/check`、`POST /api/update/apply`、`POST /api/folder/open`、`POST /api/recorder/restart`

---

## 5. 測試涵蓋

**⚠️ 2026-08-30 更正**：下面「5.2 歷史一次性驗證記錄」列出的項目，過去是用臨時 script 跑過、當時全數通過沒錯，但**從未被提交進 repo**——`git ls-files` 確認整個專案目前沒有任何測試檔案、沒有 pytest/unittest 設定、git log 也沒有任何提交碰過測試檔。也就是說在這次更正之前，這份文件描述的「約 100 項單元測試通過」其實**沒有任何一項是現在可以重新執行來驗證的**，純粹是開發當下的記錄。5.1 是這個問題發現後新建的、真正會留在 repo 裡的回歸測試；5.2 保留原文字當歷史記錄，但標註清楚「不可重跑」。

### 5.1 持久化回歸測試（`tests/` 目錄，可重複執行，2026-08-30 起）

標準庫 `unittest`，不新增第三方依賴。執行：

```
python -m unittest discover -s tests -v
```

- **`tests/test_library.py`**（32 項，1 項 `@expectedFailure`）：`canonical_anchor_name` 前綴剝離、`resolve_video`/`resolve_all_segments` 各種檔案情境（hevc/ts 轉檔/找不到）、**resolve cache 的「驗證後才信任」邏輯**（cache 命中跳過 glob、檔案被換掉時正確失效重掃）、`group_by_date` 分段計數、`build_playlist` 分段展開、`list_anchors` 分組排序。標記 `@expectedFailure` 的那項是**已知未修的 bug**（見 AGENT.md §7）：壓縮後的分段檔名 `..._hevc.mp4` 抓不到編號，尚未決定何時修。
- **`tests/test_danmaku_subtitle.py`**（19 項）：offset 過濾排序、SRT 分組/多行上限/使用者名稱前綴、ASS header/跳脫字元/多 lane、sidecar 路徑。
- **`tests/test_danmaku_store.py`**（17 項，真的開 tempfile SQLite，不是 mock）：insert/record_many、anchor/url/keyword/時間範圍篩選、count、`delete_content_matching`（含 SQL LIKE 萬用字元逸出）、anchors 統計、prune、get_store 單例。
- **`tests/test_danmaku_capture_decode.py`**（22 項，用 `tests/_protobuf_helper.py` 手刻的 encoder 產生 fixture）：`_iter_fields` varint/length-delimited、`_decode_chat_messages`（含**internalExt 誤判成 content 那個歷史 bug 的回歸樁**、非 chat 訊息類型過濾、junk 內容防呆正則）、`parse_push_frame`（含 gzip、無 field 8 fallback）、`_parse_close_frame`。
- **`tests/test_health.py`**（41 項，2026-08-31 補）：`worst()` 嚴重度排序、`evaluate_health()` 的磁碟/DB/錄製器/webui 降級四種檢查（含邊界值、多問題取最嚴重狀態）、`classify_recording`/`evaluate_recordings` 的可疑錄製偵測（含 `finished_reason=error` 強制判壞、長錄製檔案被搬走視為正常、5 筆名單截斷邏輯）、`looks_like_cookie_issue`/`evaluate_cookie_health` 的 cookie 過期文字比對。
- **`tests/test_marks_store.py`**（17 項，2026-08-31 補，回放標記功能）：新增/查詢/更新備註/刪除、時間段起訖驗證（`end_epoch` 必須晚於 `start_epoch`）、依 session 過濾與排序、get_store 單例。
- **`test_library.py` 新增 `LocateEpochInWindowsTests`**（9 項，2026-08-31 補）：回放標記讀取時把絕對 epoch 換算成「屬於哪個目前的 clip、片段內第幾秒」的核心比對邏輯，含跨片段邊界、超出範圍夾邊、片段間空隙等情境。
- **`tests/test_discord_bot.py`**（31 項，2026-08-31 補，Discord 控制頻道功能）：`parse_command` 各種指令/別名/自訂前綴、`normalize_target_url` 抖音號展開、`find_matching_entries` 精確名稱優先於網址子字串、`format_list`/`format_help` 輸出、設定讀寫 round-trip。**不含實際 Discord 連線**（跟 `danmaku_capture.py` 的 websocket 層同一個道理，協定/網路層不寫自動化測試，純邏輯部分才寫）。

合計 **188 項，跑起來 OK（1 項 expected failure）**。涵蓋 web 側七個純邏輯模組；`web_ui.py`/`main.py` 這類綁 Flask/全域狀態/真實外部行程的部分、以及 `disk_manager`/`netutil`/`priority`/`compressor`/`rec_notify` 還沒有自動化測試，仍是 5.2 描述的「臨時 script + 手動驗證」老路子。

### 5.2 歷史一次性驗證記錄（不可重跑，僅供參考）

以純邏輯設計，關鍵行為皆有單元測試（全數通過）：

- **health**：三級判定、recorder-expected 邏輯、多問題取最嚴重、壞檔分類、cookie 啟發式（22 項）
- **disk_manager**：暫停/恢復 hysteresis、清理最舊優先、保護錄製中檔、keep_days（9 項）
- **netutil**：port 偵測、pick_port 避讓、port 檔讀寫（4 項）
- **priority**：檔案快取、60 vs 300 間隔、旗標增刪共存（11 項）
- **compressor**：編碼器偵測解析、lane 解析、`enabled_encoders` 手動選、**50 檔 4 執行緒無重複領取**、總進度數學、per-lane 狀態（27 項）
- **danmaku_store**：insert/record_many、關鍵字/時間範圍/發言者篩選、排序分頁、anchors 統計、CSV 匯出、prune（18 項）
- **danmaku_capture**：protobuf varint、chat/user 解碼（含 gzip、多訊息）、websocket mask/unmask、狀態 shape（11 項）
- **danmaku_subtitle**：ASS/SRT 時間格式（進位）、offset 計算、超範圍略過、ASS lane/move、SRT 分組、sidecar 路徑、CLI 端到端（21 項）
- **library**：檔案解析（hevc/ts/缺檔）、sidecar 偵測、日期分組、清單時間排序、id 映射、目錄越界防護（21 項）；SRT→VTT 轉換
- **priority URL 正規化**：https/query/尾斜線/大小寫 match（7 項）
- **rec_notify**：開始/結束偵測、啟用後不洪水、prime 種子、時長格式、notify_start 抑制、設定存取（14 項）

合計約 100 項單元測試通過（**當時**）。新 src 模組（health / disk_manager / netutil / compressor / danmaku_*）皆可完整 `py_compile`；`web_ui.py` 亦通過整檔編譯。大型檔案偶因沙箱掛載截斷時，改以「純邏輯抽離＋獨立片段 AST 驗證＋逐段讀檔確認」補足。

> ⚠️ 唯一無法在此驗證的是**彈幕擷取層對真實抖音的連線/簽章**（協定逆向、會改版、無實時連線可測）；其餘全部可測且通過（**當時**——見本節開頭更正，這些測試現在都無法重跑）。

health/disk_manager/netutil/priority/compressor/rec_notify 這幾個目前**沒有**被收進 5.1 的持久化 `tests/`，是這次沒空一起補的部分，之後有需要可以比照 5.1 的模式（standalone `importlib` 載入、假 IO callable）補回去。

---

## 6. 設計原則（延續用）

1. **純邏輯與 IO 分離** — 判斷邏輯放在無副作用的模組（health / disk_manager / compressor 的規劃函式），方便測試、破壞性動作留在薄薄的 web 層。
2. **預設安全** — 所有新功能預設關閉或不注入預設值；破壞性操作（磁碟清理）需明確開啟並可預覽。
3. **不靜默失敗** — 出錯要嘛通知、要嘛在 UI 標紅，避免使用者數天後才發現。
4. **複用既有機制** — 警報複用 msg_push 管道、設定沿用 alerts.py 的 JSON 合併模式。

---

## 7. 下一步建議（來自 ROADMAP）

P0 穩定性批次、P1-1 設定 GUI、P1-2 統計頁（連同控制台 UI/UX 重構五階段）皆已完成。後續優先度：

1. P1-3 首次設定精靈。
2. P2（英文 i18n、遠端存取、自動化測試擴充、main.py 重構）維持「需要時再做」。
3. （待決）桌面 widget 已標記棄用，是否直接刪檔留給使用者決定。

---

## 8. 已知限制 / 注意事項

- **多線壓縮的硬碟 I/O 瓶頸** — 同時讀寫多個大檔，傳統 HDD 並行收益不如 SSD。
- **硬體編碼器偵測** — 以 `ffmpeg -encoders` 判斷「有無該編碼器」，不 100% 保證執行成功（例：無實體 NVIDIA 卡），故保留「失敗自動退回 CPU」。
- **執行階段設定檔在 recorder 同層 `config/`**（通常 `F:\main\DouyinLiveRecorder_v4.0.7\config\`），不是 source 子資料夾那個；改動或清資料時要認明路徑。
- **改前端／web_ui 後需重啟 web_ui**（`start_console.bat`；桌面 widget 已棄用，`start_widget.bat` 僅保留給仍想用舊浮動視窗的人手動執行）並 Ctrl+Shift+R，否則畫面／資料來自舊行程。
- **孤兒 ffmpeg 造成同一場直播重複錄製（已修，2026-09-24）** — 根因是重啟/部署用的 `taskkill /f /im ...exe` 沒加 `/T`，只殺主程式、留下還在錄的 ffmpeg 孤兒行程，新啟動的 exe 不知道它的存在又開一次新錄製；四處 `taskkill`（`web_ui.py`/`widget.py`/`deploy.bat`/`install_all.bat`）已加 `/T`，`main.py` 也加了「已在 `recording` 集合內就跳過」防呆。詳見 AGENT.md §7。新增 `tools/find_duplicate_sessions.py`（+ `src/dup_session_finder.py`，有單元測試）供唯讀掃描既有重複檔案，不自動搬/刪。
