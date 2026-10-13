"""FastMCP server exposing buy-smarter as tools for an MCP client (Claude).

The calling Claude is the conversational brain: it passes structured args
(city/budget/sources) and reads the markdown these tools return. The tools are
thin wrappers over the existing service/source/render layers — single source of
truth, no logic duplicated here. Nothing here spawns a nested `claude` CLI
subprocess by default (that would be wasteful and can deadlock a nested CLI);
``recommend`` uses the naive query parser unless ``parse_with_llm=True``.

Run over stdio with ``uv run python -m buysmarter.mcp_server``.
"""

import logging

from fastmcp import FastMCP
from market_sources.base import ProductSource, RawProduct
from market_sources.kaspi.client import KaspiClient
from market_sources.kaspi.source import KaspiSource
from market_sources.olx.source import OlxSource
from market_sources.ozon.browser import OzonBrowser
from market_sources.ozon.source import OzonSource

from buysmarter.cli.report import render_markdown, render_no_substance_markdown
from buysmarter.config import get_settings
from buysmarter.memory import prefs
from buysmarter.services.recommend import NoSubstanceMatch
from buysmarter.services.recommend import recommend as run_recommend

logger = logging.getLogger(__name__)

mcp: FastMCP = FastMCP("buy-smarter")

_DEFAULT_CITY = "Алматы"
_ALL_SOURCES = ("kaspi", "ozon", "olx")
# City name (RU/EN, any case) → Kaspi cityId; unknown → None (Kaspi = Almaty).
_KASPI_CITY_IDS = {
    "алматы": "750000000",
    "almaty": "750000000",
    "астана": "710000000",
    "astana": "710000000",
    "нур-султан": "710000000",
}


def _source_for(name: str) -> ProductSource:
    """Instantiate a single marketplace adapter by name.

    ``Settings`` supplies the Kaspi proxy / Ozon base URL explicitly (the library
    would otherwise fall back to ``KASPI_PROXY`` / ``OZON_BASE_URL``).
    """
    settings = get_settings()
    factories = {
        "kaspi": lambda: KaspiSource(KaspiClient(proxy=settings.kaspi_proxy)),
        "ozon": lambda: OzonSource(OzonBrowser(base_url=settings.ozon_base_url)),
        "olx": OlxSource,
    }
    if name not in factories:
        raise ValueError(f"unknown source {name!r}; use one of {', '.join(_ALL_SOURCES)}")
    return factories[name]()


def _kaspi_city_id(city: str | None) -> str | None:
    """Resolve a human city name to a Kaspi cityId (``None`` → Kaspi default)."""
    return _KASPI_CITY_IDS.get((city or "").strip().lower())


async def _aclose(source: ProductSource) -> None:
    """Release a source's pool/browser, if it exposes ``aclose`` (stubs may not)."""
    closer = getattr(source, "aclose", None)
    if closer is None:
        return
    try:
        await closer()
    except Exception as exc:  # noqa: BLE001 - teardown must never surface
        logger.warning("Source teardown failed: %s", exc)


def _fmt_price(price: float | None) -> str:
    return f"{int(price):,} ₸" if price else "-"


def _esc(value: object) -> str:
    """Escape ``|`` so a value cannot break a markdown table row."""
    return str(value).replace("|", "\\|")


def _products_table(products: list[RawProduct], limit: int) -> str:
    """Compact markdown table (title, price, rating, url), no ranking."""
    rows = ["| Title | Price | Rating | URL |", "| --- | ---: | ---: | --- |"]
    for p in products[: max(limit, 1)]:
        rating = f"{p.rating:.1f}" if p.rating is not None else "-"
        rows.append(
            f"| {_esc(p.title)} | {_fmt_price(p.price)} | {rating} | {_esc(p.url or '-')} |"
        )
    return "\n".join(rows)


@mcp.tool
async def recommend(
    query: str,
    city: str | None = None,
    budget: int | None = None,
    sources: str = "kaspi,ozon,olx",
    trusted_only: bool = False,
    top: int = 8,
    parse_with_llm: bool = False,
) -> str:
    """Recommend the best product for a shopping need, with an explained ranking.

    Runs the full buy-smarter pipeline: searches the selected Kazakh
    marketplaces, normalizes and cross-source-dedupes results, applies hard
    filters (price/city/required features), and ranks by explainable axes
    (brand tier, price vs trusted median, specs, reviews, availability). Returns
    a markdown report: the best pick with a per-axis score breakdown and
    pros/cons, role-labeled alternatives (best value / budget / premium), a top
    candidates table, and a "Б/у на OLX" used-market section when a top model is
    meaningfully cheaper second-hand.

    Args:
        query: The shopping need in natural language (RU/EN), e.g.
            "портативный ирригатор IPX7".
        city: City for availability/pricing (e.g. "Алматы"). Falls back to the
            stored ``city`` preference, then "Алматы".
        budget: Maximum price in KZT (hard filter). ``None`` = no cap.
        sources: Comma-separated marketplaces to query ("kaspi,ozon,olx").
        trusted_only: Reject products from unknown/untrusted brands.
        top: How many ranked candidates to include in the table.
        parse_with_llm: Leave ``False`` — you (the MCP client) already pass
            structured city/budget, so the fast naive parser is used. Set
            ``True`` only to force server-side LLM parsing of ``query``.
    """
    resolved_city = city or await prefs.get("city") or _DEFAULT_CITY
    selected = [s.strip().lower() for s in sources.split(",") if s.strip()]
    unknown = [s for s in selected if s not in _ALL_SOURCES]
    if unknown:
        return (
            f"No recommendation: unknown source(s) {', '.join(unknown)}; "
            f"use {', '.join(_ALL_SOURCES)}."
        )
    try:
        result = await run_recommend(
            query,
            city=resolved_city,
            budget=budget,
            use_llm=parse_with_llm,
            trusted_only=trusted_only,
            sources=selected or list(_ALL_SOURCES),
        )
    except NoSubstanceMatch as exc:
        return render_no_substance_markdown(exc)
    except ValueError as exc:
        return f"No recommendation: {exc}"
    return render_markdown(result, max(top, 1))


@mcp.tool
async def search_products(
    query: str,
    source: str = "kaspi",
    city: str = "Алматы",
    limit: int = 10,
) -> str:
    """Raw marketplace search — a quick unranked lookup, no scoring or filtering.

    Use this for fast "what's out there / what does X cost" checks. Returns a
    compact markdown table (title, price, rating, url) of the raw search hits
    from a single marketplace in listing order. For an analyzed, ranked
    recommendation use ``recommend`` instead.

    Args:
        query: Search terms (RU/EN), e.g. "ирригатор xiaomi".
        source: One marketplace: "kaspi", "ozon", or "olx".
        city: City name for availability (used by Kaspi; e.g. "Алматы").
        limit: Maximum rows to return.
    """
    source = source.strip().lower()
    src = _source_for(source)
    try:
        city_arg = _kaspi_city_id(city) if source == "kaspi" else None
        products = await src.search(query, city=city_arg)
    finally:
        await _aclose(src)
    if not products:
        return f"No results for {query!r} on {source}."
    return _products_table(products, limit)


def _compare_table(specs_by_name: dict[str, dict[str, str]]) -> str:
    """Side-by-side markdown spec table: one column per product, spec keys as rows."""
    names = list(specs_by_name)
    header = "| Spec | " + " | ".join(_esc(n) for n in names) + " |"
    divider = "| --- | " + " | ".join(["---"] * len(names)) + " |"
    keys = sorted({k for specs in specs_by_name.values() for k in specs})
    rows = [header, divider]
    for key in keys:
        cells = " | ".join(_esc(specs_by_name[name].get(key, "-")) for name in names)
        rows.append(f"| {_esc(key)} | {cells} |")
    return "\n".join(rows)


@mcp.tool
async def compare(product_urls_or_names: str) -> str:
    """Compare 2-4 products side by side on their specifications (via Kaspi).

    Takes a comma-separated list of 2-4 product names, searches each on Kaspi,
    takes the first hit per name, fetches its specs, and returns a side-by-side
    markdown spec table (one column per product, spec keys as rows) so
    differences are easy to eyeball.

    Args:
        product_urls_or_names: 2-4 product names, comma-separated, e.g.
            "Xiaomi Mijia F300, Revyline RL-650, Waterpik WP-100".
    """
    names = [n.strip() for n in product_urls_or_names.split(",") if n.strip()]
    if not 2 <= len(names) <= 4:
        return "Provide 2 to 4 comma-separated product names."
    src = _source_for("kaspi")
    specs_by_name: dict[str, dict[str, str]] = {}
    try:
        for name in names:
            hits = await src.search(name)
            if not hits:
                specs_by_name[name] = {"(not found)": "-"}
                continue
            try:
                specs_by_name[name] = await src.specs(hits[0].product_id)
            except Exception as exc:  # noqa: BLE001 - isolate per-product failure
                logger.exception("specs() failed for %r", name)
                specs_by_name[name] = {"(error)": f"specs unavailable: {type(exc).__name__}"}
    finally:
        await _aclose(src)
    return _compare_table(specs_by_name)


@mcp.tool
async def get_preferences() -> str:
    """Return the stored user preferences (default city, budget, excluded brands...).

    Reads from Postgres when available, else the local JSON fallback file.
    Returns a markdown key/value table, or a note when nothing is stored.
    """
    stored = await prefs.get_all()
    if not stored:
        return "No preferences stored yet."
    rows = ["| Key | Value |", "| --- | --- |"]
    rows += [f"| {key} | {value} |" for key, value in sorted(stored.items())]
    return "\n".join(rows)


@mcp.tool
async def set_preference(key: str, value: str) -> str:
    """Store a single user preference (e.g. key="city", value="Алматы").

    Persists to Postgres when reachable, else to the local JSON fallback file,
    so preferences survive across sessions. Common keys: ``city``, ``budget``,
    ``excluded_brands``. Returns a confirmation string.
    """
    await prefs.set(key, value)
    return f"Saved preference {key} = {value}."
