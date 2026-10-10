# Telegram IPTV Streamer — Xtream Codes edition

This version adds Xtream Codes login and channel discovery for authorized IPTV streams.

## Commands (send privately to the control bot)

- `/start` or `/help`
- `/setiptv SERVER USERNAME PASSWORD` — stores credentials in process memory and attempts to delete the credential message
- `/iptvstatus`
- `/categories`
- `/channels` — first page of the provider's returned live channel list
- `/channels CATEGORY_ID`
- `/search channel name`
- `/select STREAM_ID` — use the common `.ts` playback endpoint
- `/select STREAM_ID m3u8` — try the `.m3u8` playback endpoint
- `/setgroup -1001234567890`
- `/startstream`, `/stopstream`, `/restartstream`, `/status`

For persistence across Render restarts, set `XTREAM_SERVER`, `XTREAM_USERNAME`, and `XTREAM_PASSWORD` in Render environment variables. Environment variables are preferred over sending a password in chat. If you use `/setiptv`, only send it in a private chat with your own bot; Telegram message deletion is best-effort and cannot guarantee the command was never stored by a client or service.

## Render

1. Push this project to a private GitHub repository.
2. Create a Render Background Worker using the included Dockerfile/Blueprint.
3. Set `API_ID`, `API_HASH`, `BOT_TOKEN`, `USER_SESSION`, `OWNER_ID`.
4. Set the three `XTREAM_*` variables in Render. Do not commit them to Git.
5. Ensure your user account belongs to the destination group and is permitted to join its active video chat.
6. Start the group video chat manually.
7. Message the bot privately, use `/categories`, then `/channels CATEGORY_ID` or `/search name`, then `/select STREAM_ID`, and `/startstream`.

## Important limitations

- The provider must expose a compatible Xtream Codes-style `player_api.php` endpoint and permit your account to access it.
- Providers differ. The common `/live/username/password/stream_id.ts` and `.m3u8` URL patterns may not work for every provider.
- A `.ts` endpoint can be a live stream or a finite segment. Use a continuous live endpoint.
- The stream handler/library must be tested with your provider and Telegram group call; successful API login alone does not prove group-call playback works.
- This starter uses a simple channel listing in text, not pagination buttons. Large channel lists are capped in each reply; use categories/search.
- `/status` reports process state, not actual media health. Automatic reconnection is not implemented yet.
- Only stream content you own or have permission to rebroadcast.
- Treat session strings and IPTV credentials as secrets. Rotate them if exposed.

## Local run

Install Python 3.11+ and FFmpeg. Copy `.env.example` to `.env`, fill values, export environment variables, then run `pip install -r requirements.txt && python app.py`.
