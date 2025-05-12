# fetcher.py
import requests
from config import API_BASE

def fetch_sources_and_referer(slug: str, ep: str):
    resp = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId": f"{slug}?ep={ep}", "server": "hd-1", "category": "sub"}
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return data.get("sources", []), data.get("headers", {}).get("Referer")

def fetch_tracks(slug: str, ep: str):
    # Same endpoint carries tracks
    resp = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId": f"{slug}?ep={ep}", "server": "hd-1", "category": "sub"}
    )
    resp.raise_for_status()
    return resp.json().get("data", {}).get("tracks", [])
