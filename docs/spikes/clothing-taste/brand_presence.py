"""Spike: are the digest's brands on Kaspi at all? Cards whose title names the brand, per query."""

import asyncio

from market_sources.kaspi.client import KaspiClient
from market_sources.kaspi.source import KaspiSource

BRANDS = [
    "Adidas Samba", "Brooks", "Arc'teryx", "Helly Hansen", "Stone Island", "Barbour", "Salomon",
    "Nothomme", "Urban Tiger", "Blankgang", "Kotlovan", "Nomos", "New Balance", "The North Face",
]


async def main() -> None:
    source = KaspiSource(KaspiClient())
    try:
        for brand in BRANDS:
            cards = await source.search(brand)
            key = brand.split()[0].lower().replace("'", "")
            named = [c for c in cards if key in c.title.lower().replace("'", "")]
            prices = sorted(c.price for c in named if c.price)
            span = f"{prices[0]:.0f}–{prices[-1]:.0f} ₸" if prices else "-"
            print(f"{brand:15s} {len(named):3d} of {len(cards):3d}  {span}")
    finally:
        await source.aclose()


asyncio.run(main())
