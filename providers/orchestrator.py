import asyncio
from typing import List, Dict, Any

class SearchOrchestrator:
    def __init__(self, yt_provider, sc_provider):
        self.yt_provider = yt_provider
        self.sc_provider = sc_provider

    async def search(self, query: str, limit_per_source: int = 10) -> List[Dict[str, Any]]:
        tasks = [
            self.yt_provider.search(query, limit=limit_per_source),
            self.sc_provider.search(query, limit=limit_per_source)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        yt_tracks = results[0] if not isinstance(results[0], Exception) else []
        sc_tracks = results[1] if not isinstance(results[1], Exception) else []

        combined: List[Dict[str, Any]] = []
        max_len = max(len(yt_tracks), len(sc_tracks))
        for i in range(max_len):
            if i < len(yt_tracks):
                combined.append(yt_tracks[i])
            if i < len(sc_tracks):
                combined.append(sc_tracks[i])

        return combined
