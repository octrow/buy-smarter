"""Spike: Kaspi clothing cards with their main photo, as the background for the taste test."""

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx
from market_sources.kaspi.client import KaspiClient
from market_sources.kaspi.source import KaspiSource

OUT = Path(sys.argv[1])
QUERIES = {
    "sneakers": ["мужские кроссовки", "кроссовки мужские белые", "кроссовки мужские черные"],
    "boots": ["мужские зимние ботинки", "ботинки мужские зимние кожаные"],
    "jackets": ["мужская зимняя куртка", "мужская парка", "пуховик мужской"],
}


async def main() -> None:
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    source = KaspiSource(KaspiClient())
    rows, sizes = [], []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            for group, queries in QUERIES.items():
                seen = set()
                for query in queries:
                    for p in await source.search(query):
                        if p.product_id in seen or not p.image_url:
                            continue
                        seen.add(p.product_id)
                        name = hashlib.sha256(p.image_url.encode()).hexdigest()[:16] + ".jpg"
                        start = time.monotonic()
                        try:
                            r = await http.get(p.image_url)
                        except httpx.HTTPError as exc:
                            print("skip", p.image_url, repr(exc), flush=True)
                            continue
                        if r.status_code != 200:
                            print("skip", r.status_code, p.image_url, flush=True)
                            continue
                        (OUT / "img" / name).write_bytes(r.content)
                        sizes.append((len(r.content), time.monotonic() - start))
                        rows.append({"group": group, "query": query, "id": p.product_id,
                                     "title": p.title, "price": p.price, "url": p.url,
                                     "image_url": p.image_url, "views": len(p.image_urls), "file": name})
    finally:
        await source.aclose()
    (OUT / "kaspi.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    kb = sorted(s for s, _ in sizes)
    print(f"{len(rows)} cards; image KB median {kb[len(kb)//2]/1024:.0f}, max {kb[-1]/1024:.0f}; "
          f"fetch s median {sorted(t for _, t in sizes)[len(sizes)//2]:.2f}")
    for g in QUERIES:
        print(g, sum(r["group"] == g for r in rows))
    print("example url:", rows[0]["image_url"] if rows else None)


asyncio.run(main())
