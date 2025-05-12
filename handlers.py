# handlers.py
import os
import logging
from urllib.parse import urlparse, parse_qs

from telethon import events, Button
from fetcher import fetch_sources_and_referer, fetch_tracks
from downloader import remux_hls, download_subtitle

# in-memory state
STATE = {}

URL_EP_REGEX = r'https?://hianimez?\.to/watch/[^?\s]+[?&]ep=\d+'

def register_handlers(client):

    @client.on(events.NewMessage(pattern=URL_EP_REGEX))
    async def on_episode_link(event):
        url = event.raw_text.strip()
        p = urlparse(url)
        slug = p.path.strip("/").split("/")[-1]
        ep = parse_qs(p.query).get("ep", [None])[0]
        if not ep:
            return await event.reply("❌ No episode number found.")

        sources, referer = fetch_sources_and_referer(slug, ep)
        hls = [s for s in sources if s.get("type") == "hls"]
        if not hls:
            return await event.reply("⚠️ No HLS sources available.")

        STATE[f"{event.chat_id}:{slug}:{ep}"] = {"hls": hls, "referer": referer}

        buttons = [
            Button.inline(s.get("quality", "auto"), f"Q|{slug}|{ep}|{i}")
            for i, s in enumerate(hls)
        ]
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        await event.reply("<b>Select quality:</b>", buttons=keyboard, parse_mode="html")

    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"Q|")))
    async def on_quality(event):
        _, slug, ep, idx = event.data.decode().split("|")
        idx = int(idx)
        key = f"{event.chat_id}:{slug}:{ep}"
        info = STATE.get(key)
        if not info:
            return await event.answer("Session expired; resend link.", alert=True)

        hls_list = info["hls"]
        referer = info["referer"]
        stream = hls_list[idx]["url"]
        info["choice_idx"] = idx

        tracks = fetch_tracks(slug, ep)
        if not tracks:
            return await event.edit("⚠️ No subtitles available.")

        info["tracks"] = tracks
        buttons = [
            Button.inline(
                t.get("label", t.get("kind", "subtitle")),
                f"S|{slug}|{ep}|{i}"
            )
            for i, t in enumerate(tracks)
        ]
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        await event.edit("<b>Select subtitle:</b>", buttons=keyboard, parse_mode="html")

    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"S|")))
    async def on_subtitle(event):
        os.makedirs("downloads", exist_ok=True)

        _, slug, ep, tidx = event.data.decode().split("|")
        tidx = int(tidx)
        key = f"{event.chat_id}:{slug}:{ep}"
        info = STATE.get(key)
        if not info:
            return await event.answer("Session expired; resend link.", alert=True)

        stream = info["hls"][info["choice_idx"]]["url"]
        referer = info["referer"]
        track  = info["tracks"][tidx]

        status = await event.edit("⏳ Downloading & remuxing…", parse_mode="html")
        out_mp4 = f"downloads/{slug}_{ep}.mp4"
        remux_hls(stream, referer, out_mp4)

        await status.edit("💾 Subtitle download…", parse_mode="html")
        sub_path = download_subtitle(track, slug, ep)

        await status.edit("🚀 Uploading video…", parse_mode="html")
        await event.reply(file=out_mp4)
        await event.reply(file=sub_path)
        await status.edit("✅ Done!", parse_mode="html")
