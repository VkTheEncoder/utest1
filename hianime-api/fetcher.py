# fetcher.py
import requests
from config import API_BASE

def search_anime(query: str, page: int = 1):
    """
    GET /api/v2/hianime/search?q={query}&page={page}
    Returns data.animes: list of { id, name, poster, ... } :contentReference[oaicite:0]{index=0}
    """
    resp = requests.get(f"{API_BASE}/search", params={"q": query, "page": page})
    resp.raise_for_status()
    return resp.json().get("data", {}).get("animes", [])

def fetch_episodes(anime_id: str):
    """
    GET /api/v2/hianime/anime/{animeId}/episodes
    Returns data.episodes: 
      [ { number, title, episodeId, isFiller }, … ] :contentReference[oaicite:1]{index=1}
    """
    resp = requests.get(f"{API_BASE}/anime/{anime_id}/episodes")
    resp.raise_for_status()
    return resp.json().get("data", {}).get("episodes", [])

def fetch_sources_and_referer(episode_id: str):
    """
    GET /api/v2/hianime/episode/sources?animeEpisodeId={episodeId}&server=hd-1&category=sub
    Returns (sources, referer) :contentReference[oaicite:2]{index=2}
    """
    resp = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId": episode_id, "server": "hd-1", "category": "sub"}
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return data.get("sources", []), data.get("headers", {}).get("Referer")

def fetch_tracks(episode_id: str):
    """
    Pull the subtitles (“tracks”) from the same endpoint :contentReference[oaicite:3]{index=3}
    """
    # reuse the same call so we don’t double-scrape
    sources, referer = fetch_sources_and_referer(episode_id)
    data = requests.get(
        f"{API_BASE}/episode/sources",
        params={"animeEpisodeId": episode_id, "server": "hd-1", "category": "sub"}
    ).json().get("data", {})
    return data.get("tracks", [])
