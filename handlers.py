import logging
import asyncio

from telethon import events

from config import STATE
import fetcher
import downloader


async def register_handlers(client):
    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"EP|")))
    async def on_single_episode(event):
        episode_id = event.data.decode().split("|", 1)[1]
        await event.answer()
        # pass the Telethon client instance explicitly
        await _download_episode(event.client, event.chat_id, episode_id, ctx_event=event)

    @client.on(events.CallbackQuery(data=lambda d: d and d.startswith(b"ALL|")))
    async def on_all(event):
        await event.answer()
        chat_id = event.chat_id

        # Collect all episode IDs however you do in fetcher
        episodes = fetcher.get_all_episode_ids()
        # Initialize or overwrite the queue for this chat
        STATE.setdefault(chat_id, {})["queue"] = episodes.copy()

        await event.respond("✅ Queued all episodes. Starting downloads…")
        # Kick off the queue processor
        asyncio.create_task(_process_queue(event.client, chat_id))


async def _download_episode(client, chat_id: int, episode_id: str, ctx_event=None):
    """
    Downloads/remuxes a single episode and sends it to the user.
    If ctx_event is provided, edits that message; otherwise sends a new one.
    """
    # choose between editing an existing message or sending a fresh one
    if ctx_event:
        edit = ctx_event.edit
    else:
        edit = lambda text, **kwargs: client.send_message(chat_id, text, **kwargs)

    # notify “downloading…”
    status = await edit(f"⏳ Downloading <b>{episode_id}</b>…", parse_mode="html")

    # fetch the source URL and remux it to mp4
    url = fetcher.get_url(episode_id)
    out_mp4 = downloader.remux(url, episode_id)

    # send the resulting file
    await client.send_file(
        chat_id,
        out_mp4,
        caption=f"<b>{episode_id}</b>",
        parse_mode="html"
    )

    # clean up the “downloading” message
    await status.delete()


async def _process_queue(client, chat_id: int):
    """
    Processes the queued episode IDs one by one.
    """
    state = STATE.get(chat_id, {})
    queue = state.get("queue", [])

    while queue:
        episode_id = queue.pop(0)
        try:
            # notice we now pass `client` explicitly
            await _download_episode(client, chat_id, episode_id)
        except Exception as e:
            logging.exception("Queue download failed")
            await client.send_message(
                chat_id,
                f"❌ Failed on {episode_id}: {e}"
            )

    # when done
    await client.send_message(chat_id, "✅ All done!")
