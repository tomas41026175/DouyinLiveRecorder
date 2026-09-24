# 多路監看（Monitor）規劃

給之後動手實作用的規劃文件，先不動程式碼。

## 目標

新增一個「監看」頁面，可以**同時觀看多個目前正在錄製中的直播畫面**（不是回放已完成的錄影檔）：
下拉選單選要加入的主播、手動移除、拖曳調整每個直播畫面的排序與尺寸。

## 範圍確定：只監看「目前正在錄製中」的主播

抖音沒有獨立的「查詢是否直播中」API——查活動狀態跟解析播放網址是同一支需要簽章（`a_bogus`）的
API，且解析出來的播放網址是有時效性的 CDN token。如果不限制範圍、任何 `URL_config.ini` 裡的
主播都能加入監看，每次「加入監看」都要另外呼叫一次簽章 API，複雜度和被抖音風控盯上的風險都
higher。

**決定：monitor 只能加入 `main.py` 目前正在錄製中的主播**——網址已經解析過，直接借用，不用
再呼叫簽章 API。代價：main.py 目前完全沒有把解析出來的播放網址存到任何跨行程可讀的地方（見下）。

## 關鍵技術現況（調查結果）

- **main.py 解析出來的播放網址（`real_url`）是完全暫時性的**：`start_record()` 裡的區域變數，
  直接塞進 ffmpeg 指令列（`main.py:1310`）就結束，沒有寫進 `recording_sessions` DB、沒有寫進
  `streamer_limits.json`、沒有任何跨行程可讀的地方。`recording_sessions.live_url` 存的是房間
  頁面網址（如 `https://live.douyin.com/12345`），不是實際播放網址。
- **main.py 對同一場直播全程只解析一次網址**：`check_subprocess()`（`main.py:532-608`）啟動
  ffmpeg 後只輪詢行程是否還活著，不會中途重新解析、重啟 ffmpeg。代表這個網址在整場直播期間
  應該穩定可用，不是那種秒過期的 token——但**同一個網址能否被瀏覽器另開一條連線跟 ffmpeg
  同時播放，還沒驗證過，是需要用真實直播測試才能確認的風險點**。
- **播放格式是 FLV**：main.py 用 `stream_info.get('flv_url')`（`main.py:657-664`）。瀏覽器原生
  `<video>` 不支援 FLV，需要引入 [flv.js](https://github.com/bilibili/flv.js)（MIT 授權，B 站
  開源）在前端把 FLV 即時 remux 成 MSE 片段。這是新的前端第三方 JS，不是新增 Python 依賴，
  做法比照 `sign.js`：下載後 vendor 進 `web_static/vendor/flv.min.js`，不用 CDN 即時載入（離線
  可用、不用擔心 CDN 掛掉）。
- **main.py 改動需要重建並部署 exe** 才會生效（`CLAUDE.md` 規則）——這個功能無法只靠改 web 側
  就完成，是這次規劃跟先前功能最大的不同點，使用者已確認可以接受。

## 架構設計

### 1. main.py：把解析出來的播放網址寫成一個小的即時狀態檔

**不用 DB 欄位**，改用一個獨立的輕量 JSON 檔（例如 `config/live_status.json`），理由：
`recording_sessions` 是歷史錄製紀錄表，拿來存「只在直播當下有意義、下播就該消失」的暫時性播放
網址不乾淨，也要處理舊 DB 的 schema migration；獨立 JSON 檔簡單、不用 migration，下播時整筆
刪除語意也更直覺。

寫入時機：`main.py:1329` 附近（`real_url`/`port_info` 解析完成、`_tracker.session_start(...)`
呼叫的同一個地方）。格式草案：

```json
{
  "https://live.douyin.com/12345": {
    "anchor_name": "序号1 小美",
    "real_url": "https://xxx.douyincdn.com/xxx.flv?expire=...",
    "quality": "原画",
    "resolved_at": "2026-08-30T12:00:00"
  }
}
```

下播/ffmpeg 行程結束時，把該筆從檔案移除（或標記 `ended: true`，讓前端能顯示「已下播」而不是
整個消失打斷使用者操作，兩種都行，實作時再定）。多執行緒寫入同一個檔案要注意加鎖（比照
`streamer_limits.json` 現有的讀寫方式）。

### 2. web_ui.py：新增兩支端點

- `GET /api/monitor/live` — 回傳目前可監看的主播清單（讀 `live_status.json` + 比對
  `active_sessions()` 確保真的還在錄），給下拉選單用。
- `GET /api/monitor/stream/<key>` — 回傳該主播目前的 `real_url`（單純回傳網址字串，**不是**
  由 Flask 代理串流本身）。

**刻意不做 Flask 代理轉發影像流**：Flask 開發伺服器不擅長同時撐多條長連線的串流轉發，讓瀏覽器
的 flv.js 直接連到抖音 CDN 才是對的做法——server 端成本只有輕量的狀態查詢 API，不會因為使用者
同時看 5 個直播就讓 web_ui 變慢。

### 3. 前端：新增「監看」頁面

- 側邊欄新增「監看」路由，比照現有頁面風格。
- 頂部下拉選單（資料源 `/api/monitor/live`），選了就在下面的網格加一個新 tile；同一主播不能
  重複加入。
- 每個 tile：`<video>` 綁一個 flv.js player 實例、右上角「移除」按鈕、靜音切換（**預設除了
  第一個 tile 以外全部靜音**，避免多路同時出聲音互相干擾）。
- **拖曳排序**：目前專案唯一的拖曳慣例是 `.pl-block-resize`（回放頁的區塊寬度調整），但那個
  只做「調寬」不做「換位置」——排序是全新的機制，用滑鼠事件實作簡單的拖放（不需要額外套件）。
- **調整尺寸**：每個 tile 右下角一個 resize handle，拖曳調整寬高（比照 `.pl-block-resize` 的
  滑鼠事件模式，但作用在單一 tile 而不是整欄）。
- **版面持久化**：目前加入的主播清單、排序、每個 tile 的尺寸存 localStorage（比照 mtInit 元件
  的慣例）；重新整理頁面時，用 `/api/monitor/live` 重新驗證每個持久化的主播是不是還在錄，不在
  的話顯示「已下播」佔位或直接移除（實作時再定哪個 UX 比較好）。
- 定期輪詢 `/api/monitor/live`（例如每 30-60 秒）更新下拉選單內容、偵測已在監看的主播是否
  下播。

## 已知風險 / 待驗證清單（都需要用真實直播測試，不是純邏輯能驗證的）

1. **抖音 CDN 對 flv.js 的直接瀏覽器連線是否有 CORS 限制**——ffmpeg 抓流不受 CORS 規範，但
   瀏覽器的 `fetch`/XHR 會被擋。這是整個計畫最大的風險點，**應該最先驗證**（見下方實作順序
   Stage 3），如果被擋，可能得退回用 web_ui 代理轉發（會拉高 server 端成本，需要重新評估）。
2. **同一個播放網址能否同時被 ffmpeg 跟瀏覽器兩條連線消費**——抖音的簽章/連線限制不透明，需
   要實測，不能只憑推論。
3. flv.js 的授權/維護狀態要在動手時再次確認（B 站官方專案，長期活躍，風險應該不高）。
4. `live_status.json` 的併發寫入（多執行緒同時錄製多個主播）需要跟 `streamer_limits.json`
   一樣有鎖，避免寫壞。

## Stage 3 驗證工具（已建立，2026-08-30；2026-08-30 更新為一步式）

不用先做 main.py + exe 那一大段就能先驗證風險清單第 1、2 點。目前有兩種用法：

### 一步式（推薦）：`tools/flv_probe_server.py`

把「解析直播間網址」跟「播放測試頁」合併成同一個本機 Flask server，使用者只要貼直播間網址：

1. 執行 `python tools/flv_probe_server.py`（跟 web_ui.py/main.py 同一個 Python 環境，需要能
   `import src.spider`、Flask 已是既有依賴）。
2. 瀏覽器打開 `http://127.0.0.1:8901/`。
3. 貼上直播間網址（如 `https://live.douyin.com/123456789`），按「解析」——後端呼叫跟 main.py
   完全一樣的 `spider.get_douyin_web_stream_data()`（讀 `config/config.ini` 的
   `[Cookie] 抖音cookie`），回傳目前可用畫質的 FLV/HLS 網址。
4. 頁面自動選第一個畫質並開始播放（也可以從下拉選單換畫質，或手動貼 FLV 網址覆蓋）。

`GET /resolve?url=...` 端點只做讀取、不寫任何東西，不用改 main.py、不用重建 exe。**不支援**
`v.douyin.com` 短連結（會回傳明確錯誤訊息，請求使用者改貼網頁版房間網址）。

### 純 CLI 備案：`tools/probe_live_url.py`

跟一步式工具用同一套解析邏輯，差別是純命令列輸出、不需要瀏覽器能連到本機 8901 埠：
`python tools/probe_live_url.py https://live.douyin.com/你的直播間ID`，把印出的 FLV 網址手動
貼進 `tools/flv_probe.html` 的「手動貼 FLV 網址」欄位即可播放。適合在沒有瀏覽器環境、或想先
確認解析結果本身而不急著測播放的情況下使用；一步式工具能做的事這支也都做得到，純粹是保留一個
更輕量、依賴更少的備案。

### 共通說明

`tools/flv_probe.html` 頁面上的 log 區塊會顯示 flv.js 的事件：`STATISTICS_INFO` 持續出現代表
真的在正常播放（可驗證風險清單第 1 點 CORS 沒問題）；`ERROR` 事件則配合瀏覽器 F12 的
Network/Console 分頁看實際錯誤訊息最準確。測風險清單第 2 點（同一網址能否被 ffmpeg + 瀏覽器
同時消費），要在按下解析/播放的同時，另外用 app 正常錄製同一個房間，觀察兩邊是否互相中斷。

這些工具都是**一次性診斷用**，驗證完風險點之後就可以刪掉（或留著，不影響其他功能）；跟最終要
vendor 進 `web_static/` 的正式 flv.js 版本無關，`flv_probe.html` 現在故意直接吃 CDN 版本方便
快速測試。

## 建議實作順序

1. **main.py**：加 `live_status.json` 寫入邏輯（開始錄製時寫入、行程結束時移除/標記結束），
   重建 exe，手動驗證（直播時打開這個 JSON 檔確認內容正確）。
2. **web_ui.py**：`/api/monitor/live` + `/api/monitor/stream/<key>` 兩支端點，純讀取，風險低。
3. **最小可行驗證（單一 tile，先不做網格/拖曳/尺寸）**：vendor 進 flv.js，寫一個最簡單的頁面
   讓它接上 Stage 2 給的網址播放——**這一步就是在驗證上面風險清單的第 1、2 點**。如果卡在
   CORS 或連線衝突，先解決或調整架構，再往下做，不要在還沒驗證播放可行性之前就把網格/拖曳/
   尺寸這些 UI 全部做完。
4. **完整網格 UI**：下拉加入/移除、拖曳排序、tile 尺寸調整、localStorage 持久化、靜音預設、
   定期輪詢偵測下播。
5. **文件同步**：README（使用說明）+ ROADMAP（標完成）+ PROJECT_STATUS（補一條）+ AGENT.md
   （記錄 `live_status.json` 的格式/鎖定慣例，方便之後維護）。
6. 有新增的純邏輯（例如版面序列化/還原、tile 尺寸邊界計算）比照專案慣例補進 `tests/` 目錄。
