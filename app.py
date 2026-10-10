    if not match or not match.group(1):
        await event.reply(
            f"Current output quality: `{state['video_quality']}p`.\n"
            "Choose `/quality 360`, `/quality 480`, or `/quality 720`.\n"
            "The new setting applies the next time you start or restart playback."
        )
        return
    quality = int(match.group(1))
    state["video_quality"] = quality
    log.info("Output video quality changed to %sp", quality)
    await event.reply(
        f"Output quality set to **{quality}p**. This applies on the next `/startstream` "
        "or `/restartstream`; stop/restart playback if a stream is already running."
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
            log.info("Requesting playback in group_id=%s for stream_id=%s", group_id, state["selected_stream_id"])
            await calls.play(group_id, make_media_stream(url))
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
        f"• Output quality: `{state['video_quality']}p`\n"
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
