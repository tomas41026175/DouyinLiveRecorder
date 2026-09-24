"""
discord_bot.py - Optional Discord bot: 在指定頻道用文字指令或斜線指令新增/
移除監控中的主播（文字：`!新增 <網址或抖音號> [主播名稱]`、
`!移除 <主播名稱或網址片段>`、`!清單`；斜線：`/add` `/remove` `/list` `/help`，
兩套介面共用同一套 `_dispatch()` 邏輯）。

**斜線指令需要額外的 OAuth2 scope**：邀請 bot 加入伺服器時，產生邀請連結要
連 `bot` 跟 `applications.commands` 兩個 scope 都勾選，才能讓斜線指令同步
成功；只勾 `bot`（舊的邀請連結）的話 `tree.sync()` 會失敗或指令不會出現在
Discord 的 `/` 選單裡，需要重新產生邀請連結、重新邀請一次 bot（見
README.md「Discord 控制頻道」章節）。斜線指令會在 bot 登入成功、且已經知道
監控頻道所在的伺服器時自動同步一次（見 `on_ready`），不用手動操作。

**唯一的例外依賴**：跟專案一貫的「不新增第三方依賴」原則不同，這個功能需要
`discord.py`（要常駐連線監聽 Discord 頻道訊息，不是像 rec_notify.py 那樣單向
發 webhook）——手刻 Discord Gateway 協定（心跳/身分驗證/重連/斷線退避）成本
明顯過高，使用者已明確同意為這個功能破例（見 AGENT.md §7）。`start_console.bat`
會自動用 `pyembed\\python.exe -m pip install` 裝好，不需要使用者手動處理。

**設計**：純指令解析（`parse_command`/`normalize_target_url`/
`find_matching_entries`）完全不依賴 `discord` 套件、可獨立單元測試；連線/收發
訊息才碰 `discord.py`，且用 try/except ImportError 包起來——就算這個套件因為
任何原因沒裝成功，其餘功能（錄製/彈幕/回放…）完全不受影響，只是這個 bot 不會
啟動（`is_available()` 回 False，`start()` 回 False 並記錄原因）。

Config (config/discord_bot.json):
{
  "enabled": false,
  "token": "",
  "channel_id": "",
  "prefix": "!"
}

執行緒模型：`start()` 開一個 daemon thread 呼叫 `discord.Client.run(token)`（這
個呼叫本身是阻塞的，內部會自己建一個新的 asyncio event loop 跑在這個 thread
裡，不會跟 Flask 的同步 request 處理搶主執行緒）。新增/移除/清單三個操作都是
一般同步的檔案 IO（跟 web_ui.py 既有的 URL_config.ini 讀寫共用同一個
`threading.Lock`），從 discord.py 的 async handler 裡用
`loop.run_in_executor(None, ...)` 丟到執行緒池執行，避免卡住 Gateway 的心跳。
"""
from __future__ import annotations

import asyncio
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import common as _common

try:
    import discord  # type: ignore
except Exception:
    discord = None


DEFAULT_DISCORD_BOT = {
    "enabled": False,
    "token": "",
    "channel_id": "",
    "prefix": "!",
}


def is_available() -> bool:
    """True if `discord.py` imported successfully."""
    return discord is not None


def load_settings(path) -> Dict[str, Any]:
    return _common.load_json_settings(path, DEFAULT_DISCORD_BOT)


def save_settings(path, data: Dict[str, Any]) -> None:
    _common.save_json_settings(path, data, DEFAULT_DISCORD_BOT)


# ---------------------------------------------------------------------------
# Pure command parsing (no discord.py / IO involved -- unit-testable)
# ---------------------------------------------------------------------------
_ADD_RE = re.compile(r"^(?:新增|加)\s+(\S+)(?:\s+(.+))?$")
_REMOVE_RE = re.compile(r"^(?:移除|刪除|删除)\s+(.+)$")
_LIST_WORDS = {"清單", "列表", "list"}
_HELP_WORDS = {"help", "說明", "幫助", "指令"}


def normalize_target_url(raw: str) -> str:
    """A bare 抖音號（沒有 scheme）會被展開成 live.douyin.com 房間網址 -- 跟控
    制台「新增主播」欄位的慣例一致（index.html #f-url 的 placeholder 提示）。
    已經是完整網址（http/https 開頭）的話原樣返回。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if re.match(r"^https?://", raw):
        return raw
    return f"https://live.douyin.com/{raw}"


def parse_command(content: str, prefix: str) -> Optional[Dict[str, Any]]:
    """把一則 Discord 訊息解析成指令 dict，不是辨識得出的指令就回傳 None（靜
    默忽略，讓頻道還能正常聊天，不會每則訊息都跳錯誤回覆）。

    回傳格式：
      {"action": "add", "url": "...", "anchor_name": "..."}
      {"action": "remove", "target": "..."}
      {"action": "list"}
      {"action": "help"}
    """
    content = (content or "").strip()
    if not prefix or not content.startswith(prefix):
        return None
    body = content[len(prefix):].strip()
    if not body:
        return None

    m = _ADD_RE.match(body)
    if m:
        return {"action": "add", "url": normalize_target_url(m.group(1)),
                "anchor_name": (m.group(2) or "").strip()}

    m = _REMOVE_RE.match(body)
    if m:
        return {"action": "remove", "target": m.group(1).strip()}

    if body.lower() in _LIST_WORDS:
        return {"action": "list"}
    if body.lower() in _HELP_WORDS:
        return {"action": "help"}
    return None


def find_matching_entries(entries: List[Dict[str, Any]], target: str) -> List[Dict[str, Any]]:
    """把 `移除` 指令的自由文字 `target` 拿去比對主播清單。先精確比對
    anchor_name（不分大小寫）；完全比對得到的話就直接採用，不管 URL 是否也能
    比對到，讓「打對主播名稱」永遠是最不會誤判的方式（除非真的有兩個主播同
    名）。精確比對沒有結果，才退而求其次比對 URL 是否包含這段文字（子字串），
    方便使用者直接貼一段房間網址代替名稱。"""
    target = (target or "").strip()
    if not target:
        return []
    exact = [e for e in entries
             if (e.get("anchor_name") or "").strip().lower() == target.lower()]
    if exact:
        return exact
    return [e for e in entries if target.lower() in (e.get("url") or "").lower()]


def format_help(prefix: str) -> str:
    return ("文字指令：\n"
            f"`{prefix}新增 <網址或抖音號> [主播名稱]`\n"
            f"`{prefix}移除 <主播名稱或網址片段>`\n"
            f"`{prefix}清單`\n"
            "斜線指令（輸入 `/` 選單也會出現）：\n"
            "`/add <url> [name]`　`/remove <target>`　`/list`　`/help`")


def format_list(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return "目前沒有設定任何主播"
    lines = []
    for e in entries:
        if e.get("is_recording"):
            mark = "\U0001F534"       # 🔴 錄製中
        elif not e.get("enabled", True):
            mark = "⏸️"     # ⏸️ 已停用
        else:
            mark = "⚪"          # ⚪ 待命
        lines.append(f"{mark} {e.get('anchor_name') or '(未命名)'} — {e.get('url', '')}")
    text = "\n".join(lines)
    if len(text) > 1900:  # Discord 單則訊息上限 2000 字，留餘裕
        text = text[:1900] + "\n…（清單太長，已截斷）"
    return text


# ---------------------------------------------------------------------------
# Networking (needs discord.py) -- kept separate from the pure parsing above
# ---------------------------------------------------------------------------
_bot_thread: Optional[threading.Thread] = None
_client = None  # type: ignore
_connected = False
_last_error: Optional[str] = None
_generation = 0


def _preflight_token_check(token: str) -> str:
    """在呼叫 discord.py 的 `client.run()` 之前，用最陽春的 `urllib`（預設會
    自動套用 Windows 登錄檔/環境變數裡設定的系統 proxy，行為跟使用者拿
    PowerShell `Invoke-RestMethod` 直接測 token 時一致）打一次同一支 Discord
    REST API（`GET /users/@me`，跟 discord.py 內部 `static_login()` 打的是
    同一個 endpoint）。

    這是排查 `LoginFailure: Improper token has been passed.` 的關鍵一步：
    discord.py 底層用 aiohttp，且從未替 `discord.Client` 設定 `proxy=`，所以
    aiohttp 完全不會自動套用系統 proxy（跟 urllib/PowerShell 不同）。如果這裡
    用 urllib 打同一個 token 成功、但 discord.py 隨後仍回報 LoginFailure，就
    幾乎可以確定是「這台機器連 Discord 需要 proxy，但 aiohttp 沒有拿到」，而
    不是 token 本身有問題——只回傳一行文字記錄到 log，不影響後續實際登入。"""
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {token}"},
    )
    proxies = urllib.request.getproxies()
    proxy_in_use = proxies.get("https") or proxies.get("http") or "(無，直連)"
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "ignore")[:200]
            return f"成功 HTTP {resp.status}，系統 proxy={proxy_in_use}，回應={body}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        return f"失敗 HTTP {e.code}，系統 proxy={proxy_in_use}，回應={body}"
    except Exception as e:
        return f"失敗 {type(e).__name__}: {e}，系統 proxy={proxy_in_use}"


def status() -> Dict[str, Any]:
    return {
        "available": is_available(),
        "running": bool(_bot_thread and _bot_thread.is_alive()),
        "connected": _connected,
        "last_error": _last_error,
    }


def stop() -> None:
    """Best-effort disconnect of a running bot (e.g. before applying new
    settings). Safe to call even if nothing is running.

    `discord.Client.loop` is NOT `None` before the client actually starts
    connecting -- discord.py initializes it to its own internal `utils.
    MISSING` sentinel object, which has none of asyncio's loop methods. A
    plain `is not None` check treats that sentinel as "a real loop" and
    `.is_closed()` then blows up with AttributeError (hit in practice: saving
    settings twice in a row, or saving right after enabling, calls stop() on
    a client that's been constructed but hasn't gotten far enough into
    run() to receive its real loop yet). Guard with an isinstance check
    instead of a None check.

    Also forgets `_bot_thread` so a subsequent `start()` can always spawn a
    fresh thread, even if this one never actually exits (observed in
    practice: a thread stuck resolving DNS / opening the Gateway connection
    with no timeout does not react to `client.close()` and `is_alive()`
    keeps returning True forever -- that previously made every future
    reconnect attempt silently no-op via `start()`'s "already running" check,
    so editing/fixing the token and saving again could never take effect
    without a full process restart). It's a daemon thread, so an orphaned
    stuck one just dies with the process; leaking one is far better than
    permanently blocking all future reconnects."""
    global _client, _bot_thread
    c = _client
    _client = None
    _bot_thread = None
    if c is None:
        return
    loop = getattr(c, "loop", None)
    if not isinstance(loop, asyncio.AbstractEventLoop) or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(c.close(), loop)
    except Exception:
        pass


def start(settings: Dict[str, Any], callbacks: Dict[str, Callable]) -> bool:
    """啟動背景 bot thread（若已停用/缺設定/套件沒裝好就直接跳過，回傳 False
    並記錄原因到 `_last_error`，可用 status() 查）。`callbacks` 需要三個 key：
      "add":    (url, anchor_name) -> (ok: bool, message: str)
      "remove": (url) -> (ok: bool, message: str)
      "list":   () -> List[entry dict]（至少要有 url/anchor_name/enabled/
                 is_recording 幾個欄位，跟 web_ui.py list_entries() 一致）

    每次呼叫都會遞增 `_generation`，舊執行緒（若還卡著沒真的結束）回呼時會
    比對這個編號，編號不符就不再更動全域的 `_connected`/`_last_error`，避免
    一個卡死的舊連線稍後才回報失敗，蓋掉新連線已經成功的狀態。
    """
    global _bot_thread, _last_error, _generation
    if not settings.get("enabled"):
        _last_error = None
        return False
    token = (settings.get("token") or "").strip()
    channel_id = str(settings.get("channel_id") or "").strip()
    if not token or not channel_id:
        _last_error = "缺少 token 或 channel_id"
        return False
    if discord is None:
        _last_error = "discord.py 未安裝（重啟 web_ui 讓啟動腳本自動安裝，或手動 pip install discord.py）"
        return False
    if _bot_thread and _bot_thread.is_alive():
        return True  # 已經在跑了（呼叫方應先 stop() 再 start() 來強制換新的）

    prefix = settings.get("prefix") or "!"
    _generation += 1
    gen = _generation

    def _run():
        global _client, _last_error, _connected
        print(f"[discord_bot] (#{gen}) 準備登入，token 長度={len(token)}，"
              f"前2碼={token[:2]!r} 後2碼={token[-2:]!r}")
        _diag = _preflight_token_check(token)
        print(f"[discord_bot] (#{gen}) 直連 Discord REST API 預檢：{_diag}")

        intents = discord.Intents.default()
        intents.message_content = True  # 讀訊息文字內容必須的權限，記得在
        # Discord Developer Portal 的 Bot 設定頁打開「MESSAGE CONTENT INTENT」

        client = _ControlClient(channel_id, prefix, callbacks, gen, intents=intents)
        _client = client
        try:
            client.run(token, log_handler=None)
        except Exception as e:  # 連線被拒/token 錯誤等
            if gen == _generation:
                _last_error = f"{type(e).__name__}: {e}"
            print(f"[discord_bot] (#{gen}) 連線失敗: {type(e).__name__}: {e}")
        finally:
            if gen == _generation:
                _connected = False

    _bot_thread = threading.Thread(target=_run, daemon=True, name=f"discord-control-bot-{gen}")
    _bot_thread.start()
    return True


if discord is not None:
    class _ControlClient(discord.Client):  # pragma: no cover -- needs a real
        # Discord connection, exercised manually not via unittest (跟
        # danmaku_capture.py 的 websocket 連線同一個道理：協定/網路層不寫
        # 自動化測試，純邏輯部分才寫，見 tests/test_discord_bot.py)。
        def __init__(self, channel_id: str, prefix: str,
                    callbacks: Dict[str, Callable], gen: int = 0, **kw):
            super().__init__(**kw)
            self._channel_id = str(channel_id)
            self._prefix = prefix
            self._callbacks = callbacks
            self._gen = gen
            self.tree = discord.app_commands.CommandTree(self)
            self._register_slash_commands()

        def _register_slash_commands(self):
            """斜線指令（`/add` `/remove` `/list` `/help`）。指令名稱用英文
            是因為 Discord 對指令名稱字元有限制、且改名字要重新同步，用英文
            比較不會踩到未來的相容性問題；說明文字跟實際回覆維持中文。邏輯
            直接組成跟文字指令 (`!新增`...) 一樣的 cmd dict 丟給 `_dispatch()`
            執行，兩種介面共用同一套行為，不重複維護。"""

            @self.tree.command(name="add", description="新增監控主播")
            @discord.app_commands.describe(url="網址或抖音號", name="主播名稱（選填）")
            async def _slash_add(interaction: "discord.Interaction", url: str, name: str = ""):
                await self._handle_slash(
                    interaction,
                    {"action": "add", "url": normalize_target_url(url), "anchor_name": name},
                )

            @self.tree.command(name="remove", description="移除監控主播")
            @discord.app_commands.describe(target="主播名稱或網址片段")
            async def _slash_remove(interaction: "discord.Interaction", target: str):
                await self._handle_slash(interaction, {"action": "remove", "target": target})

            @self.tree.command(name="list", description="列出目前監控的主播")
            async def _slash_list(interaction: "discord.Interaction"):
                await self._handle_slash(interaction, {"action": "list"})

            @self.tree.command(name="help", description="顯示可用指令")
            async def _slash_help(interaction: "discord.Interaction"):
                await self._handle_slash(interaction, {"action": "help"})

        async def _handle_slash(self, interaction: "discord.Interaction", cmd: Dict[str, Any]):
            if str(interaction.channel_id) != self._channel_id:
                await interaction.response.send_message(
                    "❌ 這個指令只能在監控頻道使用", ephemeral=True)
                return
            await interaction.response.defer()
            loop = asyncio.get_event_loop()
            reply = await loop.run_in_executor(None, self._dispatch, cmd)
            if reply:
                try:
                    await interaction.followup.send(reply)
                except Exception as e:
                    print(f"[discord_bot] 回覆斜線指令失敗: {e}")

        async def on_ready(self):
            global _connected
            if self._gen == _generation:
                _connected = True
            print(f"[discord_bot] (#{self._gen}) 已登入: {self.user}"
                  f"（監控頻道 id={self._channel_id}）")
            # 斜線指令要先 sync 過一次 Discord 才會顯示出來。同步到「特定伺服
            # 器」是即時生效；同步成全域指令的話 Discord 官方文件說可能要等
            # 最多 1 小時才會出現在所有地方，所以這裡優先抓監控頻道所在的
            # 伺服器做 guild sync，個人自用單一伺服器場景這樣最省事。
            try:
                channel = self.get_channel(int(self._channel_id))
                guild = getattr(channel, "guild", None)
                if guild is not None:
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    print(f"[discord_bot] (#{self._gen}) 已同步 {len(synced)} 個斜線指令"
                          f"到伺服器「{guild.name}」")
                else:
                    synced = await self.tree.sync()
                    print(f"[discord_bot] (#{self._gen}) 已同步 {len(synced)} 個全域斜線指令"
                          f"（找不到頻道所屬伺服器，改用全域同步，可能要等最多 1 小時才會出現）")
            except Exception as e:
                print(f"[discord_bot] (#{self._gen}) 同步斜線指令失敗: {type(e).__name__}: {e}")

        async def on_message(self, message):
            if message.author.bot:
                return
            if str(message.channel.id) != self._channel_id:
                return
            cmd = parse_command(message.content, self._prefix)
            if cmd is None:
                return
            loop = asyncio.get_event_loop()
            reply = await loop.run_in_executor(None, self._dispatch, cmd)
            if reply:
                try:
                    await message.channel.send(reply)
                except Exception as e:
                    print(f"[discord_bot] 回覆訊息失敗: {e}")

        def _dispatch(self, cmd: Dict[str, Any]) -> Optional[str]:
            action = cmd["action"]
            try:
                if action == "add":
                    if not cmd["url"]:
                        return f"❌ 請提供網址或抖音號，例如：`{self._prefix}新增 https://live.douyin.com/123456 小美`"
                    ok, msg = self._callbacks["add"](cmd["url"], cmd.get("anchor_name", ""))
                    return ("✅ " if ok else "❌ ") + msg
                if action == "remove":
                    entries = self._callbacks["list"]()
                    matches = find_matching_entries(entries, cmd["target"])
                    if not matches:
                        return f"❌ 找不到符合「{cmd['target']}」的主播"
                    if len(matches) > 1:
                        names = "、".join(e.get("anchor_name") or e["url"] for e in matches)
                        return f"⚠️ 符合多筆，請打更精確的名稱：{names}"
                    ok, msg = self._callbacks["remove"](matches[0]["url"])
                    return ("✅ " if ok else "❌ ") + msg
                if action == "list":
                    return format_list(self._callbacks["list"]())
                if action == "help":
                    return format_help(self._prefix)
            except Exception as e:
                return f"❌ 發生錯誤：{type(e).__name__}: {e}"
            return None
