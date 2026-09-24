# CLAUDE.md

給 Claude Code / AI agent 的專案須知。**權威工程說明在 [`AGENT.md`](AGENT.md)**，本檔只補 Claude 常忘的重點。開工前務必讀 `AGENT.md`。

## 一分鐘上手

- 這是**個人自用**的抖音直播錄製工具（上游 DouyinLiveRecorder v4.0.7 + 自訂層）。
- 有**兩個執行體**：`DouyinLiveRecorder.exe`（由 `main.py` 打包）與 **Web UI**（`web_ui.py`，跑原始碼）。分清楚你改的是哪個。
- 使用者說明在 `README.md`，路線在 `ROADMAP.md`，進度在 `PROJECT_STATUS.md`。

## 動手前必記（最常踩的雷）

1. **改 `main.py` 或 recorder 用的 `src/*` → 一定要 `build_main_exe.bat` 重建 exe** 才生效。改 `web_ui.py` / `web_static/` / web 側 `src/*` → 重啟 web_ui 即可（`start_console.bat`）。
2. **執行階段設定在安裝目錄 `config\`**（`F:\main\DouyinLiveRecorder_v4.0.7\config\`），**不是** source 子資料夾那個。
3. **`.bat` 一律寫純 ASCII**——繁中 cmd 用 Big5 讀檔，UTF-8 中文會被誤判成指令分隔符而報錯。
4. **不新增第三方依賴**——用標準庫（recorder 用精簡可攜 Python）。
5. **純邏輯與 IO 分離 + 附單元測試**（見 `AGENT.md` §5、§8）。
6. **對外請求帶 User-Agent**（Discord/Cloudflare 會擋無 UA）；HTML `<video>` 字幕要 WebVTT（SRT 需轉）。

## 慣用工作流

- 改碼後 `python -m py_compile <檔>` 驗語法；純邏輯寫臨時測試 script（monkeypatch 攔 IO）。
- 動到大型檔（web_ui.py/main.py/compressor.py）若用遠端 sandbox，掛載副本可能截斷導致 `py_compile` 誤報——複製到 /tmp 再測，或讀實檔逐段確認。
- 新功能完成後**同步更新文件**：README（教學）+ ROADMAP（標完成）+ PROJECT_STATUS（補一條）。

## 目前主要功能（都已完成、在 Web UI）

儀表板健康監測 + 主動警報、每主播錄製上限 + 特別關注（60s 快抓開播）、磁碟自動管理、H.265 多編碼器並行壓縮、彈幕擷取/查詢/轉字幕、依日期連續回放 + 回放標記（時間點/時間段 + 備註，可跨同一 session 相鄰片段）、Discord 錄製通知、Discord 控制頻道（文字指令新增/移除主播，唯一使用第三方依賴 `discord.py` 的功能）。細節見 `AGENT.md` §4 模組地圖。
