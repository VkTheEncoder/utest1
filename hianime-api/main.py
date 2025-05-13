# hianime-api/main.py

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from config import ANIWATCH_API_BASE as API_BASE

# Import your existing modules here:
# from . import fetcher, episodes, sources, tracks
import fetcher      # adjust the import paths if needed
import episodes
import sources
import tracks

app = FastAPI(title="Hianimez Local API")

# Allow CORS from anywhere (for simplicity)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1) Health endpoint
@app.get("/health")
async def health():
    return {"status": "ok"}


# 2) Search
@app.get("/api/v2/hianime/search")
async def search(q: str, page: int = 1):
    try:
        return fetcher.search_anime(q, page)
    except Exception as e:
        raise HTTPException(502, str(e))


# 3) Episodes
@app.get("/api/v2/hianime/episodes/{anime_id}")
async def get_episodes(anime_id: str):
    try:
        return episodes.fetch_episodes(anime_id)
    except Exception as e:
        raise HTTPException(502, str(e))


# 4) Sources
@app.get("/api/v2/hianime/sources/{episode_id}")
async def get_sources(episode_id: str):
    try:
        sources_list, _ = sources.fetch_sources_and_referer(episode_id)
        return sources_list
    except Exception as e:
        raise HTTPException(502, str(e))


# 5) Tracks
@app.get("/api/v2/hianime/tracks/{episode_id}")
async def get_tracks(episode_id: str):
    try:
        return tracks.fetch_tracks(episode_id)
    except Exception as e:
        raise HTTPException(502, str(e))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 4000)),
        reload=True
    )
