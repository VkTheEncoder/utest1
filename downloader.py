# downloader.py
import subprocess, os, requests

def remux_hls(m3u8_url: str, referer: str | None, out_path: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = ["ffmpeg", "-y"]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += ["-i", m3u8_url, "-c", "copy", out_path]
    subprocess.run(cmd, check=True)

def download_subtitle(track: dict, out_dir: str, base_name: str) -> str:
    """
    track: { lang, file (URL) }
    Saves to out_dir/{base_name}_{lang}.vtt
    """
    lang = track.get("label", track.get("lang", "subtitle")).split()[0]
    fname = f"{base_name}_{lang}.vtt"
    path = os.path.join(out_dir, fname)
    os.makedirs(out_dir, exist_ok=True)
    resp = requests.get(track["file"])
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
    return path
