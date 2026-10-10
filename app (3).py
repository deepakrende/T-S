import asyncio
import logging
import os
import re
from urllib.parse import urlparse, urlunparse

import aiohttp
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("iptv-streamer")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
USER_SESSION = os.environ["USER_SESSION"]
OWNER_ID = int(os.environ["OWNER_ID"])
DEFAULT_GROUP_ID = os.getenv("GROUP_ID", "").strip()

bot = TelegramClient("control_bot", API_ID, API_HASH)
user = TelegramClient(StringSession(USER_SESSION), API_ID, API_HASH)
calls = None

state = {
    "group_id": int(DEFAULT_GROUP_ID) if DEFAULT_GROUP_ID else None,
    "xtream_server": os.getenv("XTREAM_SERVER", "").rstrip("/"),
    "xtream_username": os.getenv("XTREAM_USERNAME", ""),
    "xtream_password": os.getenv("XTREAM_PASSWORD", ""),
    "categories": [],
    "channels": {},
    "selected_stream_id": None,
    "selected_channel_name": None,
    "stream_url": None,
    "running": False,
}
control_lock = asyncio.Lock()


def private_owner(event):
    return event.sender_id == OWNER_ID and event.is_private


def normalized_server(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Server must be a complete http:// or https:// URL.")
    return value


def credentials_ready() -> bool:
    return all(state[k] for k in ("xtream_server", "xtream_username", "xtream_password"))


def safe_channel_name(name: str, limit: int = 80) -> str:
    return " ".join(str(name or "Unnamed channel").split())[:limit]


async def xtream_api(action: str, **params):
    if not credentials_ready():
        raise RuntimeError("IPTV credentials are not configured. Use /setiptv in a private chat.")
    url = state["xtream_server"] + "/player_api.php"
    query = {
        "username": state["xtream_username"],
        "password": state["xtream_password"],
        "action": action,
        **params,
    }
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=query) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
    if isinstance(data, dict) and data.get("user_info", {}).get("auth") == 0:
        raise RuntimeError("IPTV provider rejected the login.")
    if not isinstance(data, list) and action.startswith("get_live_"):
        raise RuntimeError("Unexpected response from provider API.")
    return data


async def load_categories():
    result = await xtream_api("get_live_categories")
    state["categories"] = result
    return result


async def load_channels(category_id=None):
    params = {}
    if category_id is not None:
        params["category_id"] = str(category_id)
    result = await xtream_api("get_live_streams", **params)
    state["channels"] = {str(c.get("stream_id")): c for c in result if c.get("stream_id") is not None}
    return list(state["channels"].values())


def make_stream_url(stream_id: str, extension: str = "ts") -> str:
    # Xtream live endpoints commonly use /live/username/password/stream_id.ts.
    # The provider may require a different endpoint or extension.
    server = state["xtream_server"]
    username = state["xtream_username"]
    password = state["xtream_password"]
    return f"{server}/live/{username}/{password}/{stream_id}.{extension}"


async def start_selected_stream(event):
    async with control_lock:
        if state["running"]:
            await event.reply("A stream is already running. Use `/stopstream` first.")
            return
        if not state["group_id"]:
            await event.reply("Set a destination first with `/setgroup -100...`.")
            return
        if not state["stream_url"]:
            await event.reply("Select a channel first with `/channels` then `/select STREAM_ID`.")
            return
        group_id = state["group_id"]
        url = state["stream_url"]
        await event.reply("Connecting to the active group video chat and starting playback…")
        try:
            await calls.play(group_id, MediaStream(url))
            state["running"] = True
            await event.reply(
                f"✅ Playback requested for **{state['selected_channel_name'] or 'selected channel'}**. "
                "Check the active group video chat."
            )
        except Exception as exc:
            log.exception("Could not start stream")
            await event.reply(
                "❌ Playback could not start. Verify that the group video chat is active, "
                "the user account can join, and your IPTV provider permits playback from Render. "
                f"Error type: `{type(exc).__name__}`."
            )


def help_text():
    return (
        "📡 **IPTV Group-Call Streamer**\n\n"
        "**IPTV setup (private chat only):**\n"
        "`/setiptv https://provider.example username password`\n"
        "`/iptvstatus` — show whether IPTV login is configured\n"
        "`/categories` — list live-TV categories\n"
        "`/channels` — list first channels across all categories\n"
        "`/channels CATEGORY_ID` — list channels in a category\n"
        "`/search channel name` — search channel list\n"
        "`/select STREAM_ID` — choose a channel\n\n"
        "**Telegram streaming:**\n"
        "`/setgroup -1001234567890`\n"
        "`/startstream` · `/stopstream` · `/restartstream` · `/status`\n\n"
        "Use only content you are authorized to stream. Credentials are held in memory unless "
        "configured as Render environment variables."
    )


@bot.on(events.NewMessage(pattern=r"^/start(?:@\w+)?$"))
@bot.on(events.NewMessage(pattern=r"^/help(?:@\w+)?$"))
async def help_command(event):
    if private_owner(event):
        await event.reply(help_text(), link_preview=False)


@bot.on(events.NewMessage(pattern=r"^/setiptv(?:@\w+)?(?:\s+(.+))?$"))
async def setiptv_command(event):
    if not private_owner(event):
        return
    raw = event.raw_text.strip()
    # Delete the message promptly because it contains the provider password.
    try:
        await event.delete()
    except Exception:
        pass
    match = re.match(r"^/setiptv(?:@\w+)?\s+(\S+)\s+(\S+)\s+(\S+)$", raw)
    if not match:
        await bot.send_message(
            event.chat_id,
            "Usage: `/setiptv https://provider.example username password`\n"
            "Send this only in a private chat with your bot. The command message is deleted when possible.",
        )
        return
    try:
        server = normalized_server(match.group(1))
    except ValueError as exc:
        await bot.send_message(event.chat_id, str(exc))
        return
    state["xtream_server"] = server
    state["xtream_username"] = match.group(2)
    state["xtream_password"] = match.group(3)
    state["categories"] = []
    state["channels"] = {}
    state["selected_stream_id"] = None
    state["selected_channel_name"] = None
    state["stream_url"] = None
    await bot.send_message(
        event.chat_id,
        "IPTV credentials saved in this process's memory. Now run `/iptvstatus` or `/categories`. "
        "For persistence across Render restarts, configure `XTREAM_SERVER`, `XTREAM_USERNAME`, "
        "and `XTREAM_PASSWORD` in Render environment variables.",
    )


@bot.on(events.NewMessage(pattern=r"^/iptvstatus(?:@\w+)?$"))
async def iptvstatus_command(event):
    if not private_owner(event):
        return
    if credentials_ready():
        await event.reply(f"IPTV login configured for `{urlparse(state['xtream_server']).netloc}`.")
    else:
        await event.reply("IPTV login not configured. Use `/setiptv SERVER USERNAME PASSWORD` in this private chat.")


@bot.on(events.NewMessage(pattern=r"^/categories(?:@\w+)?$"))
async def categories_command(event):
    if not private_owner(event):
        return
    try:
        categories = await load_categories()
        if not categories:
            await event.reply("No live-TV categories were returned by the provider.")
            return
        lines = ["**Live-TV categories** (use `/channels CATEGORY_ID`):"]
        for item in categories[:80]:
            lines.append(f"• `{item.get('category_id', '?')}` — {safe_channel_name(item.get('category_name'))}")
        if len(categories) > 80:
            lines.append(f"…and {len(categories) - 80} more. Use the provider category IDs.")
        await event.reply("\n".join(lines))
    except Exception as exc:
        log.warning("Category fetch failed: %s", type(exc).__name__)
        await event.reply(f"Could not fetch categories: `{type(exc).__name__}`. Check credentials and provider API access.")


@bot.on(events.NewMessage(pattern=r"^/channels(?:@\w+)?(?:\s+(\d+))?$"))
async def channels_command(event):
    if not private_owner(event):
        return
    match = re.match(r"^/channels(?:@\w+)?(?:\s+(\d+))?$", event.raw_text.strip())
    category_id = match.group(1) if match else None
    try:
        channels = await load_channels(category_id)
        if not channels:
            await event.reply("No channels returned. Try `/categories` or check the category ID.")
            return
        title = f"**Channels in category {category_id}**" if category_id else "**First channels returned by provider**"
        lines = [title, "Use `/select STREAM_ID` to choose one:"]
        for channel in channels[:60]:
            lines.append(f"• `{channel.get('stream_id')}` — {safe_channel_name(channel.get('name'))}")
        if len(channels) > 60:
            lines.append(f"…and {len(channels) - 60} more. Use `/search name` or a category.")
        await event.reply("\n".join(lines))
    except Exception as exc:
        log.warning("Channel fetch failed: %s", type(exc).__name__)
        await event.reply(f"Could not fetch channels: `{type(exc).__name__}`. Check credentials and provider API access.")


@bot.on(events.NewMessage(pattern=r"^/search(?:@\w+)?\s+(.+)$"))
async def search_command(event):
    if not private_owner(event):
        return
    term = event.pattern_match.group(1).strip().casefold()
    try:
        # Load all live streams, then search names locally.
        channels = await load_channels()
        found = [c for c in channels if term in str(c.get("name", "")).casefold()]
        if not found:
            await event.reply("No matching channels found. Try a shorter search term or `/categories`.")
            return
        lines = ["**Matching channels** — use `/select STREAM_ID`:"]
        for channel in found[:60]:
            lines.append(f"• `{channel.get('stream_id')}` — {safe_channel_name(channel.get('name'))}")
        if len(found) > 60:
            lines.append(f"…{len(found) - 60} more matches.")
        await event.reply("\n".join(lines))
    except Exception as exc:
        log.warning("Search failed: %s", type(exc).__name__)
        await event.reply(f"Search failed: `{type(exc).__name__}`.")


@bot.on(events.NewMessage(pattern=r"^/select(?:@\w+)?\s+(\d+)(?:\s+(ts|m3u8))?$"))
async def select_command(event):
    if not private_owner(event):
        return
    stream_id = event.pattern_match.group(1)
    extension = event.pattern_match.group(2) or "ts"
    channel = state["channels"].get(stream_id)
    if channel is None:
        try:
            channels = await load_channels()
            channel = state["channels"].get(stream_id)
        except Exception as exc:
            await event.reply(f"Could not load channel list: `{type(exc).__name__}`.")
            return
    if channel is None:
        await event.reply("Stream ID not found in the latest channel list. Run `/channels` or `/search name` first.")
        return
    state["selected_stream_id"] = stream_id
    state["selected_channel_name"] = safe_channel_name(channel.get("name"))
    state["stream_url"] = make_stream_url(stream_id, extension)
    await event.reply(
        f"Selected **{state['selected_channel_name']}** (ID `{stream_id}`).\n"
        f"Playback extension: `{extension}`.\n"
        "Use `/startstream` to broadcast. If your provider requires a different format, try "
        "`/select STREAM_ID m3u8`."
    )


@bot.on(events.NewMessage(pattern=r"^/setgroup(?:@\w+)?(?:\s+(-?\d+))?$"))
async def setgroup_command(event):
    if not private_owner(event):
        return
    match = re.match(r"^/setgroup(?:@\w+)?(?:\s+(-?\d+))?$", event.raw_text.strip())
    if not match or not match.group(1):
        await event.reply("Usage: `/setgroup -1001234567890`")
        return
    async with control_lock:
        if state["running"]:
            await event.reply("Stop the current stream before changing the group.")
            return
        state["group_id"] = int(match.group(1))
    await event.reply(f"Destination group set to `{state['group_id']}`.")


@bot.on(events.NewMessage(pattern=r"^/startstream(?:@\w+)?$"))
async def startstream_command(event):
    if not private_owner(event):
        return
    await start_selected_stream(event)


@bot.on(events.NewMessage(pattern=r"^/stopstream(?:@\w+)?$"))
async def stopstream_command(event):
    if not private_owner(event):
        return
    async with control_lock:
        if not state["group_id"] or not state["running"]:
            await event.reply("No stream is marked as running.")
            return
        try:
            await calls.leave_call(state["group_id"])
        except Exception:
            log.exception("Leave call failed")
        state["running"] = False
    await event.reply("⏹️ Stream stopped and call left.")


@bot.on(events.NewMessage(pattern=r"^/restartstream(?:@\w+)?$"))
async def restartstream_command(event):
    if not private_owner(event):
        return
    async with control_lock:
        if not state["group_id"] or not state["stream_url"]:
            await event.reply("Set a group and select a channel first.")
            return
        group_id = state["group_id"]
        url = state["stream_url"]
        try:
            if state["running"]:
                await calls.leave_call(group_id)
            state["running"] = False
            await calls.play(group_id, MediaStream(url))
            state["running"] = True
        except Exception as exc:
            log.exception("Stream restart failed")
            await event.reply(f"❌ Restart failed: `{type(exc).__name__}`. Check Render logs.")
            return
    await event.reply("🔄 Stream restart requested.")


@bot.on(events.NewMessage(pattern=r"^/status(?:@\w+)?$"))
async def status_command(event):
    if not private_owner(event):
        return
    source_name = state["selected_channel_name"] or "Not selected"
    await event.reply(
        "**Streamer status**\n"
        f"• Group: `{state['group_id'] or 'Not set'}`\n"
        f"• Channel: `{source_name}`\n"
        f"• Stream ID: `{state['selected_stream_id'] or 'Not set'}`\n"
        f"• Playback flag: `{'running' if state['running'] else 'stopped'}`\n"
        "The playback flag is process state, not an independent media-health check."
    )


async def main():
    global calls

    # Initialize PyTgCalls inside the same asyncio event loop used by Telethon.
    calls = PyTgCalls(user)

    await user.start()
    me = await user.get_me()
    log.info("Authenticated user session as user_id=%s", me.id)

    await bot.start(bot_token=BOT_TOKEN)
    await calls.start()

    log.info("Bot and PyTgCalls started")
    print("Bot is running. Send /start privately from the configured OWNER_ID account.")

    await bot.run_until_disconnected()


if __name__ == "__main__":
    # Telethon clients are created at module import time and bind to the
    # thread's current event loop. Reuse that same loop instead of creating
    # a second one with asyncio.run().
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        # Keep shutdown orderly if the process exits.
        if not loop.is_closed():
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
