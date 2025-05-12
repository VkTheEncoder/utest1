# downloader.py
import subprocess
import requests
import os

def remux_hls(m3u8_url: str, referer: str | None, out_path: str):
    cmd = ["ffmpeg", "-y"]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", m3u8_url, "-c", "copy", out_path]
    subprocess.run(cmd, check=True)

def download_subtitle(track: dict, slug: str, ep: str):
    url = track["file"]
    label = track.get("label", track.get("kind", "subtitle")).replace(" ", "_")
    path = f"downloads/{slug}_{ep}_{label}.vtt"
    os.makedirs("downloads", exist_ok=True)
    r = requests.get(url)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path
