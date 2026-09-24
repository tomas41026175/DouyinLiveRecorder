# 回放頁效能優化計畫

**進度**：P0-1（resolve cache）已完成並通過單元測試（見下方說明 + `AGENT.md` §7）。
P0-2（彈幕計數批次化）尚未開始，依計畫分開做、分開觀察。

**額外發現（跟本計畫無關，實作 P0-1 測試時順便發現的既有 bug）**：
`resolve_all_segments()` 的分段編號正則抓不到已被壓縮器改名的分段檔（`a_002_hevc.mp4`
這種「數字後面不是直接接副檔名」的檔名），可能導致分段錄製的 session 一旦分段被壓縮，
回放頁清單會少列出那些分段。這個問題獨立於效能優化，需要另外決定要不要修、什麼時候修，
細節見 `AGENT.md` §7。

給之後動手實作用的規劃文件，先不動程式碼。對應症狀：

1. 點擊主播後，若該主播場次很多，`/api/library/dates` 明顯延遲。
2. 一進入回放頁（尚未選主播）也有同樣的延遲，資料要等一下才出現。

## 根因分析

### 根因 A（最大瓶頸）：每次都重新掃硬碟找真實檔案

`group_by_date()`（`src/library.py:223-251`）對傳入的每一筆 session 都會呼叫
`resolve_all_segments()`（`src/library.py:121-`）。只要那筆 session 是分段錄製
（檔名存的是 `..._%03d.ts` 樣板），`resolve_all_segments()` 就要對「壓縮版 mp4
+ 每種可播放副檔名 + 各自的壓縮版」共約 17 種候選路徑各跑一次
`glob.glob()`（真的磁碟掃描），而且**完全沒有 cache**——同一筆 session 每次
API 呼叫都重新掃一次。

- 沒選主播時：`_sessions_for_anchor('')` 回傳最近 2000 筆 session 全部丟進
  `group_by_date()`，掃描量最大 → 對應症狀 2（一進頁面就慢）。
- 選了主播：雖然會先用 Python 過濾成該主播的 session 再丟進
  `group_by_date()`，但常態分段錄製的主播場次一多，一樣要重新掃一次磁碟
  → 對應症狀 1（場次多=明顯延遲），且跟主播篩選與否無關，篩選只是縮小 N，
  不是解掉「每次都重掃」這件事本身。

`/api/library/day`（載入當天清單）走的是 `build_playlist()`，同樣依賴
`resolve_video()`/`resolve_all_segments()`，有一樣的問題。

### 根因 B：三個 API 各自重複查一次 SQLite

`_finished_sessions_for_library()`（`web_ui.py:2112`）沒有 anchor 層級的 SQL
篩選，`/api/library/anchors`、`/api/library/dates`、`/api/library/day` 三個
端點各自獨立呼叫一次（`_sessions_for_anchor()` 內部也是呼叫它），等於一次
「點主播」互動要重複跑 3 次幾乎一樣的 `SELECT ... LIMIT 2000`。SQL 本身有
`ORDER BY start_time` 應該吃得到索引，影響比根因 A 小，但仍是浪費。

### 根因 C：當天清單的「有彈幕」判斷是 N+1 查詢

`/api/library/day` 對清單裡**每一個 clip** 各自呼叫一次
`_clip_has_danmaku()`（`web_ui.py:2286-2307`），也就是每個 clip 一條獨立
SQL 打進彈幕資料庫。分段錄製把一筆 session 拆成越多段，查詢次數越多；且跟
彈幕擷取協調器共用 `danmaku_store.py` 的同一把 `threading.Lock`，擷取正在
運作時可能互相等待，放大延遲感。

### 根因 D：前端序列等待，看不到能平行的機會

`initPlayer()`（`web_static/index.html:2137-2146`）用序列 `await`：先等
`/api/library/anchors` 回來才發 `/api/library/dates`，但這兩個請求彼此完全
不依賴。若各要花 300ms~1s+（因為根因 A/B/C），使用者感受到的「進頁空白時間」
變成兩者相加，而不是取較大值。

## 優化方案（依效益排序）

| # | 方案 | 對應根因 | 效益 | 需要重建 exe？ |
|---|------|---------|------|--------------|
| P0-1 | `resolve_video`/`resolve_all_segments` 的掃硬碟結果加行程內 cache（key=stored_path），已結束的錄製檔案位置幾乎不會再變，只有轉檔/壓縮完成那一刻才需要失效一次 | A | 最大：直接砍掉兩個症狀共同的瓶頸 | 否，只動 `src/library.py` |
| P0-2 | `_clip_has_danmaku()` 從「每個 clip 一條 SQL」改成「這一天的時間範圍只查一次彈幕，Python 裡按 clip 時間窗分桶計數」 | C | 中～大，查詢數從 N 降到 1 | 否，只動 `web_ui.py` |
| P1-1 | 前端 `initPlayer()` 把 `/api/library/anchors` 與 `/api/library/dates`（anchor 為空）用 `Promise.all` 平行發送 | D | 中，省掉第一段序列等待 | 否，只動 `web_static/index.html` |
| P1-2 | `/api/library/dates`、`/api/library/day` 回應加一層短 TTL（15~30 秒）行程內 cache，當作 P0-1 之外的保險 | A、B | 中，對「來回切換同一主播/日期」特別有感 | 否 |
| P2 | main.py／compressor.py 轉檔或壓縮**完成當下**直接把真實檔案路徑寫回 `recording_sessions.file_path`（或新增欄位），之後讀取變成純 DB 查詢，glob 掃描只當 fallback | A（根本解） | 最大，且不再需要 P0-1 的 cache 失效邏輯 | 是，main.py 屬於 recorder 側 |

## 建議實作順序

1. P0-1（`src/library.py` 加 cache）——純函式好測試，跟既有 pure logic +
   unit test 慣例一致。
2. P0-2（`web_ui.py` 彈幕計數批次化）——注意批次查詢的 anchor/url 篩選邏輯
   要維持「url 優先於 anchor」（跟上次修的 `_generate_danmaku_subtitle`
   一致，否則又會出現「清單說有彈幕、實際查不到」的落差）。
3. P1-1（前端平行化）——小改動，跟 1、2 互不衝突可以並行做。
4. P1-2（TTL cache 保險層）——視 1、2 做完後實測延遲是否已經足夠，再決定
   要不要加。
5. P2（長期根本解）——排到 web 側都穩定之後，另外規劃，牽動 recorder 側
   + exe 重建，且要處理「正在錄製中／轉檔中」跟舊資料的相容性。

## 風險與注意事項（含具體避免方式）

- **P0-1 的 cache 失效風險**：如果轉檔/壓縮把舊檔換成新檔而 cache 沒失效，
  回放頁會指向已經被刪除的舊檔——跟之前修過的 `%03d` 樣板 bug 是同一類
  風險。**避免方式：「驗證後才信任」而不是「無條件信任」**——cache 只存
  「哪個 stored_path 對應到哪個真實路徑」這個對應關係本身，不代表結果
  永久有效；每次讀 cache 時，先對快取住的那個路徑做一次**便宜的**
  `os.path.exists()`（單一 syscall，不是重新 glob），還在就直接用、不用
  重掃；如果不在了（代表被轉檔/壓縮換掉了），才觸發一次真正的 glob 重新
  解析並更新 cache。這樣完全不需要 recorder 端額外發訊號通知 web_ui，
  也不會有「TTL 還沒到、但檔案已經被換掉」的空窗期。測試要涵蓋「session
  剛結束、正在轉檔」這個交界情況（轉檔前後各查一次，確認都拿到正確路徑）。
- **P0-2 的批次查詢一致性風險**：批次版本的時間窗/篩選邏輯如果跟現有單筆
  版本（`_clip_has_danmaku`）寫兩份、各自維護，遲早會像上次
  `_generate_danmaku_subtitle` 那樣兩邊邏輯drift 出現落差。**避免方式：
  抽成同一個共用的純函式**（例如 `_danmaku_count_for_window(rows, since,
  until)`），單筆版跟批次版都呼叫這個函式，只是輸入的 `rows` 一個是查一次
  資料庫拿全部、一個是每個 clip 各自查——篩選/計數邏輯只寫一份。並且補一個
  單元測試：對同一組合成資料，比較「批次版逐一算出的結果」跟「N+1 版逐一
  查出的結果」是否完全一致，往後改動任何一邊都會被測試擋下來。
- **一般降風險做法**：P0-1、P0-2 都寫成純邏輯 + 單元測試（跟專案既有慣例
  一致），先各自獨立驗證過，再接進 `web_ui.py`／`library.py`；上線後兩者
  都是行程內快取／單次查詢，重啟 web_ui 就等於清空重來，沒有跨重啟的殘留
  風險。建議 P0-1 做完先單獨觀察一段時間（尤其是轉檔中的 session）沒問題，
  再做 P0-2，不要兩個一起改，方便出問題時能分清楚是哪一個造成的。
- P2 影響面最大，不建議跟 P0/P1 一起做；先讓 web 側的優化上線觀察一段時間，
  確認效果與穩定性後再排。
