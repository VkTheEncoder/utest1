import os
import time
import subprocess
import requests
from urllib.parse import urljoin

def estimate_total_bytes(m3u8_url: str, referer: str | None) -> int:
    """HEAD‐ping each .ts segment in the playlist to sum Content-Length."""
    headers = {"Referer": referer} if referer else {}
    text = requests.get(m3u8_url, headers=headers).text
    base = m3u8_url.rsplit("/", 1)[0] + "/"
    total = 0
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        seg = urljoin(base, line.strip())
        head = requests.head(seg, headers=headers)
        if head.status_code == 200:
            total += int(head.headers.get("Content-Length", 0))
    return total

def remux_with_progress(
    m3u8_url: str,
    referer: str | None,
    out_path: str,
    progress_callback
):
    """
    Run ffmpeg to remux HLS→MP4, and every second call:
      progress_callback(transferred_bytes, total_bytes, start_time)
    so you can edit your status message.
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    total = estimate_total_bytes(m3u8_url, referer)

    cmd = ["ffmpeg", "-y"]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", m3u8_url, "-c", "copy", out_path]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)

    start = time.time()
    # Poll until ffmpeg exits
    while proc.poll() is None:
        transferred = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        progress_callback(transferred, total, start)
        time.sleep(1)

    # Final update
    transferred = os.path.getsize(out_path)
    progress_callback(transferred, total, start)
    return out_path

def download_subtitle(track: dict, out_dir: str, base_name: str) -> str:
    """
    Download a single VTT track:
      track["file"] → URL
      track["label"] → e.g. "English"
    Saves to: out_dir/{base_name}_{label}.vtt
    """
    os.makedirs(out_dir, exist_ok=True)
    label = track.get("label", track.get("kind","subtitle")).split()[0]
    fname = f"{base_name}_{label}.vtt"
    path = os.path.join(out_dir, fname)
    resp = requests.get(track["file"])
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
    return path
