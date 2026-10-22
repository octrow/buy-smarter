"""Characterization of ``recommend()`` before category profiles and capabilities (phase 1).

Twelve scenarios, each with ``use_llm`` on and off, recorded from the code before any
phase-1 change (``characterization.json``). Phase 1 moves today's category branches onto
capabilities; with an exact category match the ranking must stay the same: order, scores,
roles, rejection reasons, the best pick's pros and cons, axis explanations, used listings
and flags. The expected values are never edited to make a refactor pass: a failure means the
code changed behaviour (docs/plans/2026-10-22-capabilities-phase1.md, step 1). A deliberate fix
re-records only the entries it changes and names them in its commit (the dedupe fix #13
re-recorded ``sneakers_official_store``: Puma RS-X is no longer merged into Smash).

Only stable seams are faked: marketplace sources, the knowledge table row, Jev's HTTP
post, the LLM fallbacks inside ``llm/`` (they fail), Kimovil's lookup and the official
store page fetch.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from market_sources.base import RawOffer, RawProduct
from market_sources.kimovil.parse import KimovilPhone
from market_sources.kimovil.source import KimovilSource

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.llm import adapter, canonicalize, ingredients, jev, relevance
from buysmarter.llm.adapter import LlmError
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services import official_stores
from buysmarter.services.official_stores import StoreOffer
from buysmarter.services.recommend import NoNewPick, NoSubstanceMatch, recommend
from tests.fakes import FakeSource

EXPECTED_PATH = Path(__file__).with_name("characterization.json")
_SCORE_TOLERANCE = 0.01


# --- fakes ------------------------------------------------------------------


class _FakeJev:
    """Answers Jev's questions by plain rules over the request state.

    Relevance: a title with one of ``drop_words`` scores 0.1, the rest 0.9. Ingredients:
    a substance whose name or alias is in the title or composition scores 0.9, and the
    first percentage is picked as its concentration; ``fail_ingredients`` makes that
    request fail. Choices (category match, OLX canonicalization) pick a known model the
    title names, else the no-match option.
    """

    def __init__(self) -> None:
        self.drop_words: tuple[str, ...] = ("игрушечн", "чехол")
        self.fail_ingredients = False

    async def post(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        state, questions = body["state"], body["questions"]
        if "items" in state:
            if self.fail_ingredients:
                raise LlmError("ingredient Jev down (characterization)")
            return {"answers": self._ingredients(state, questions)}
        return {"answers": {key: self._answer(state, key, q) for key, q in questions.items()}}

    def _answer(self, state: dict[str, Any], key: str, question: dict[str, Any]) -> dict[str, Any]:
        if question["type"] == "noul":
            title = state["titles"][int(key[1:])].lower()
            return {"noul": 0.1 if any(w in title for w in self.drop_words) else 0.9}
        options = [c for c in question["criteria"] if c != "none of these"]
        title = state["titles"][int(key[1:])].lower() if "titles" in state else ""
        pick = next((c for c in options if title and c.lower() in title), "none of these")
        return {"choice": pick, "confidence": 0.9, "probabilities": {pick: 0.9}}

    def _ingredients(
        self, state: dict[str, Any], questions: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        answers: dict[str, dict[str, Any]] = {}
        for key, question in questions.items():
            item = state["items"][int(key[1:].split("s")[0])]
            text = f"{item['title']} {item['composition']}".lower()
            if question["type"] == "noul":
                substance = state["substances"][int(key.split("s")[1])]
                names = [substance["name"], *substance["aliases"]]
                answers[key] = {"noul": 0.9 if any(n.lower() in text for n in names) else 0.1}
            else:
                pick = next(c for c in question["criteria"] if c != "none of these")
                answers[key] = {"choice": pick, "confidence": 0.9, "probabilities": {pick: 0.9}}
        return answers


async def _llm_down(*_args: Any, **_kwargs: Any) -> Any:
    raise LlmError("LLM fallback down (characterization)")


@pytest.fixture
def fake_jev(monkeypatch: pytest.MonkeyPatch) -> _FakeJev:
    fake = _FakeJev()
    monkeypatch.setattr(jev, "api_key", lambda: "test-key")
    monkeypatch.setattr(jev, "_post", fake.post)
    monkeypatch.setattr(adapter, "claude_available", lambda: True)  # an LLM route exists
    for module in (ingredients, relevance, canonicalize):
        monkeypatch.setattr(module, "generate", _llm_down)
    return fake


# --- data -------------------------------------------------------------------


def _kp(pid: str, title: str, price: float | None, **kw: Any) -> RawProduct:
    return RawProduct(source="kaspi", product_id=pid, title=title, price=price, **kw)


def _olx(pid: str, title: str, price: float) -> RawProduct:
    return RawProduct(source="olx", product_id=pid, title=title, price=price)


_USED = {"condition": "Б/у", "city": "Алматы"}


async def _store(slug: str, **record: Any) -> None:
    payload = {"generated_by": "test", "updated_at": "2026-10-22T00:00:00+00:00", **record}
    async with async_session() as session:
        await repo.upsert_category_knowledge(session, slug, payload)


def _brands(tiers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"tiers": tiers, **extra}


_ADAPALENE_BOOK = {
    "substances": [
        {
            "canonical": "адапален",
            "aliases": ["adapalene"],
            "equivalents": [{"name": "Клензит"}, {"name": "Дифферин"}],
        }
    ]
}
_COSMETICS_BRANDS = _brands({"Galderma": "A", "Bioderma": "A", "Клензит": "B", "Garnier": "C"})


async def _composition_record(slug: str) -> None:
    await _store(
        slug,
        brand_book=_COSMETICS_BRANDS,
        model_book={"entries": []},
        category_type="composition_driven",
        ingredient_book=_ADAPALENE_BOOK,
    )


async def _run(query: str, constraints: QueryConstraints, use_llm: bool, **kw: Any) -> Any:
    return await recommend(
        query,
        use_llm=use_llm,
        parsed=constraints,
        persist=False,
        with_review_summary=False,
        **kw,
    )


# --- scenarios --------------------------------------------------------------


async def _spec_driven(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    await _store(
        "пылесос",
        brand_book=_brands({"Dyson": "A", "Samsung": "B", "Xiaomi": "C"}),
        model_book={
            "entries": [
                {"canonical": "Dyson V8", "brand": "Dyson", "reference_price": 160000},
            ]
        },
        category_type="spec_driven",
    )
    kaspi = FakeSource(
        "kaspi",
        [
            _kp(
                "k1",
                "Пылесос Dyson V8 Absolute",
                150000,
                brand="Dyson",
                rating=4.8,
                review_count=120,
            ),
            _kp(
                "k2",
                "Пылесос Xiaomi G10 вертикальный",
                60000,
                brand="Xiaomi",
                rating=4.6,
                review_count=300,
            ),
            _kp("k3", "Пылесос Samsung VS15", 45000, brand="Samsung", rating=4.5, review_count=80),
            _kp("k4", "Пылесос беспроводной вертикальный", 25000, rating=4.9, review_count=15),
            _kp("k5", "Аккумулятор для пылесоса Dyson V8", 12000, rating=4.7, review_count=40),
            _kp("k6", "Пылесос игрушечный детский", 5000, rating=4.2, review_count=9),
        ],
        spec_map={"k1": {"Тип": "вертикальный"}, "k3": {"Мощность": "1800 Вт"}},
        offer_map={"k1": [RawOffer(source="kaspi", product_id="k1", seller="Dyson", price=149000)]},
    )
    constraints = QueryConstraints(search_phrase="пылесос", budget_max=200000, city="Алматы")
    return await _run("пылесос до 200000", constraints, use_llm, source=kaspi)


def _cosmetics_kaspi(by_query: dict[str, list[RawProduct]], main: list[RawProduct]) -> FakeSource:
    specs = {
        "bio": {"Состав": "вода, глицерин, ниацинамид"},
        "kl": {"Состав": "адапален 0,1%, карбомер"},
        "df": {"Состав": "adapalene 0,3%"},
        "ga": {"Состав": "вода, салициловая кислота 2%"},
        "ad": {},
        "gm": {"Состав": "вода, глицерин"},
    }
    return FakeSource("kaspi", main, by_query=by_query, spec_map=specs)


_MOISTURIZERS = [
    _kp("bio", "Bioderma Sebium Hydra крем", 12000, brand="Bioderma", rating=4.8, review_count=60),
    _kp("ga", "Garnier Чистая кожа крем", 4000, brand="Garnier", rating=4.5, review_count=200),
]


async def _composition_extracted(
    use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch
) -> Any:
    await _composition_record("крем-с-адапаленом")
    kaspi = _cosmetics_kaspi(
        {
            "Клензит": [_kp("kl", "Клензит гель с адапаленом", 6000, rating=4.7, review_count=90)],
            "Дифферин": [
                _kp("df", "Дифферин крем", 9000, brand="Galderma", rating=4.9, review_count=40)
            ],
        },
        _MOISTURIZERS,
    )
    constraints = QueryConstraints(search_phrase="крем с адапаленом", budget_max=20000)
    return await _run("крем с адапаленом", constraints, use_llm, source=kaspi)


async def _composition_unchecked(
    use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch
) -> Any:
    fake.fail_ingredients = True
    return await _composition_extracted(use_llm, fake, monkeypatch)


async def _composition_no_match(
    use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch
) -> Any:
    await _composition_record("крем-с-адапаленом")
    empty: list[RawProduct] = []
    kaspi = _cosmetics_kaspi(
        {"адапален": empty, "Клензит": empty, "Дифферин": empty, "adapalene": empty},
        [*_MOISTURIZERS, _kp("gm", "Крем увлажняющий для лица", 3000, rating=4.1, review_count=7)],
    )
    constraints = QueryConstraints(search_phrase="крем с адапаленом")
    return await _run("крем с адапаленом", constraints, use_llm, source=kaspi)


async def _composition_protected(
    use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch
) -> Any:
    fake.drop_words = ("гель",)
    await _composition_record("крем-с-адапаленом")
    kaspi = _cosmetics_kaspi(
        {"Клензит": [_kp("kl", "Клензит гель с адапаленом", 6000, rating=4.7, review_count=90)]},
        [
            *_MOISTURIZERS,
            _kp("gm", "Крем увлажняющий для лица", 3000, rating=4.1, review_count=7),
            _kp("gu", "Гель для умывания очищающий", 2500, rating=4.3, review_count=30),
            _kp("cr", "Крем ночной восстанавливающий", 7000, rating=4.6, review_count=12),
        ],
    )
    constraints = QueryConstraints(search_phrase="крем с адапаленом", budget_max=20000)
    return await _run("крем с адапаленом", constraints, use_llm, source=kaspi)


def _phone(name: str, ki: float, hz: int) -> KimovilPhone:
    slug = name.lower().replace(" ", "-")
    return KimovilPhone(
        full_name=name,
        slug=slug,
        url=f"https://www.kimovil.com/en/where-to-buy-{slug}",
        ki=ki,
        specs={"Refresh rate": f"Refresh rate {hz} Hz", "Battery capacity": "4500 mAh"},
    )


async def _smartphone(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    phones = {
        "apple iphone 13": _phone("Apple iPhone 13", 78, 60),
        "samsung galaxy s23": _phone("Samsung Galaxy S23", 85, 120),
    }

    async def _lookup(self: Any, query: str) -> KimovilPhone | None:
        return phones.get(query)

    monkeypatch.setattr(KimovilSource, "lookup", _lookup)
    await _store(
        "смартфон",
        brand_book=_brands({"Apple": "A", "Samsung": "A", "Xiaomi": "B"}),
        model_book={"entries": []},
        category_type="spec_driven",
    )
    kaspi = FakeSource(
        "kaspi",
        [
            _kp(
                "ip",
                "Смартфон Apple iPhone 13 128Gb черный",
                300000,
                brand="Apple",
                rating=4.9,
                review_count=900,
            ),
            _kp(
                "sg",
                "Смартфон Samsung Galaxy S23 8/256Gb",
                350000,
                brand="Samsung",
                rating=4.8,
                review_count=400,
            ),
            _kp(
                "xr",
                "Смартфон Xiaomi Redmi Note 13 8/256Gb",
                110000,
                brand="Xiaomi",
                rating=4.7,
                review_count=1200,
            ),
        ],
    )
    constraints = QueryConstraints(
        search_phrase="смартфон", budget_max=400000, required_features=["120 Гц"]
    )
    return await _run("смартфон 120 Гц", constraints, use_llm, source=kaspi)


_SHOE_BRANDS = _brands({"Nike": "A", "Adidas": "A", "Puma": "B"})


def _shoes_kaspi() -> FakeSource:
    return FakeSource(
        "kaspi",
        [
            _kp(
                "n1",
                "Кроссовки Nike Air Max 90 мужские",
                45000,
                brand="Nike",
                rating=4.8,
                review_count=70,
            ),
            _kp(
                "a1",
                "Кроссовки Adidas Grand Court женские",
                30000,
                brand="Adidas",
                rating=4.7,
                review_count=50,
            ),
            _kp(
                "p1",
                "Кроссовки Puma Smash v2 Leather 42 белые",
                28000,
                brand="Puma",
                rating=4.6,
                review_count=20,
                category_path=["Обувь", "Мужская обувь", "Кроссовки"],
            ),
            _kp(
                "p2",
                "Кроссовки Puma Carina 2.0 Lux",
                26000,
                brand="Puma",
                rating=4.5,
                review_count=15,
                category_path=["Обувь", "Женская обувь", "Кроссовки"],
            ),
            _kp("x1", "Кроссовки беговые сетка", 9000, rating=4.2, review_count=11),
        ],
    )


async def _sneakers(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    await _store("мужские-кроссовки", brand_book=_SHOE_BRANDS, category_type="spec_driven")
    constraints = QueryConstraints(
        search_phrase="мужские кроссовки 42 размер",
        budget_max=50000,
        required_features=["42 размер"],
    )
    return await _run(
        "мужские кроссовки 42 размер до 50000", constraints, use_llm, source=_shoes_kaspi()
    )


async def _sneakers_olx(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    await _store("мужские-кроссовки", brand_book=_SHOE_BRANDS, category_type="spec_driven")
    olx = FakeSource(
        "olx",
        [
            _olx("u1", "Кроссовки Nike Air Max 90 мужские", 15000),
            _olx("u2", "Кроссовки Nike женские розовые", 10000),
            _olx("u3", "Кроссовки Adidas Superstar", 12000),
            _olx("u4", "Кроссовки детские Puma", 5000),
        ],
        spec_map={pid: _USED for pid in ("u1", "u2", "u3", "u4")},
    )
    constraints = QueryConstraints(search_phrase="мужские кроссовки", budget_max=50000)
    return await _run(
        "мужские кроссовки до 50000",
        constraints,
        use_llm,
        sources=["kaspi", "olx"],
        source=_shoes_kaspi(),
        olx_source=olx,
    )


async def _sneakers_official(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    offers = [
        StoreOffer(name="Кроссовки женские Carina", url="https://puma.kz/w1", price=12000),
        StoreOffer(name="Кроссовки Smash мужские", url="https://puma.kz/m1", price=20000),
        StoreOffer(name="Кроссовки RS-X", url="https://puma.kz/m2", price=25000),
        StoreOffer(name="Кроссовки детские Courtflex", url="https://puma.kz/k1", price=9000),
        StoreOffer(name="Кроссовки Suede Classic", url="https://puma.kz/m3", price=30000),
        StoreOffer(name="Кроссовки Future мужские", url="https://puma.kz/m4", price=45000),
    ]

    async def _fetch(client: Any, url: str) -> list[StoreOffer]:
        return list(offers)

    monkeypatch.setattr(official_stores, "fetch_store_offers", _fetch)
    book = _brands(
        {"Nike": "A", "Adidas": "A", "Puma": "A"},
        official_stores={"Puma": "https://puma.kz/men"},
    )
    await _store("мужские-кроссовки", brand_book=book, category_type="spec_driven")
    constraints = QueryConstraints(search_phrase="мужские кроссовки", budget_max=50000)
    return await _run("мужские кроссовки до 50000", constraints, use_llm, source=_shoes_kaspi())


async def _two_violations(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    await _store(
        "наушники",
        brand_book=_brands({"Sony": "A", "JBL": "A", "Marshall": "B", "Soundcore": "B"}),
        category_type="spec_driven",
    )
    kaspi = FakeSource(
        "kaspi",
        [
            _kp(
                "j1",
                "Наушники JBL Live 770NC беспроводные накладные",
                60000,
                brand="JBL",
                rating=4.7,
                review_count=50,
            ),
            _kp("j2", "Наушники JBL Tune 520BT", 20000, brand="JBL", rating=4.6, review_count=80),
            _kp(
                "s1",
                "Накладные наушники Sony WH-CH520 синие",
                25000,
                brand="Sony",
                rating=4.5,
                review_count=60,
            ),
            _kp(
                "s2",
                "Беспроводные наушники Sony WH-1000XM4 Black",
                45000,
                brand="Sony",
                rating=4.9,
                review_count=300,
            ),
            _kp(
                "q1",
                "Наушники Soundcore Q20i",
                15000,
                brand="Soundcore",
                rating=4.4,
                review_count=25,
            ),
            _kp(
                "m1",
                "Наушники Marshall Major IV",
                None,
                brand="Marshall",
                rating=4.8,
                review_count=40,
            ),
        ],
        spec_map={
            "j1": {"Шумоподавление": "ANC"},
            "s1": {"Шумоподавление": "нет"},
            "s2": {"Шумоподавление": "ANC, адаптивное"},
        },
    )
    constraints = QueryConstraints(
        search_phrase="наушники",
        budget_max=50000,
        required_features=["ANC"],
        excluded_brands=["JBL"],
    )
    return await _run("наушники с ANC до 50000 без JBL", constraints, use_llm, source=kaspi)


async def _olx_only(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    await _store(
        "робот-пылесос",
        brand_book=_brands({"Xiaomi": "B", "Roborock": "A", "Dreame": "A"}),
        category_type="spec_driven",
    )
    kaspi = FakeSource(
        "kaspi",
        [
            _kp("r1", "Робот пылесос Xiaomi S10", 80000, brand="Xiaomi"),
            _kp("r2", "Робот пылесос Roborock Q7", 120000, brand="Roborock"),
            _kp("r3", "Робот пылесос Dreame D9", 90000, brand="Dreame"),
        ],
    )
    olx = FakeSource(
        "olx",
        [
            _olx("u1", "Робот пылесос Xiaomi S10", 25000),
            _olx("u2", "Робот пылесос Roborock S5", 60000),
            _olx("u3", "Робот пылесос Dreame D9 на запчасти", 8000),
        ],
        spec_map={pid: _USED for pid in ("u1", "u2", "u3")},
    )
    constraints = QueryConstraints(search_phrase="робот пылесос", budget_max=30000)
    return await _run(
        "робот пылесос до 30000",
        constraints,
        use_llm,
        sources=["kaspi", "olx"],
        source=kaspi,
        olx_source=olx,
    )


async def _concentration(use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> Any:
    await _composition_record("крем-с-адапаленом")
    kaspi = _cosmetics_kaspi(
        {
            "Клензит": [_kp("kl", "Клензит гель адапален 0,1%", 6000, rating=4.7, review_count=90)],
            "Дифферин": [
                _kp("df", "Дифферин крем 0,3%", 9000, brand="Galderma", rating=4.9, review_count=40)
            ],
            "адапален": [_kp("ad", "Адаклин гель адапален", 5000, rating=4.3, review_count=10)],
        },
        _MOISTURIZERS,
    )
    constraints = QueryConstraints(search_phrase="крем с адапаленом", required_features=["0,1%"])
    return await _run("крем с адапаленом 0,1%", constraints, use_llm, source=kaspi)


SCENARIOS: dict[str, Any] = {
    "spec_driven": _spec_driven,
    "composition_extracted": _composition_extracted,
    "composition_unchecked": _composition_unchecked,
    "composition_no_match": _composition_no_match,
    "composition_protected": _composition_protected,
    "smartphone_kimovil": _smartphone,
    "sneakers_gender_size": _sneakers,
    "sneakers_olx_audience": _sneakers_olx,
    "sneakers_official_store": _sneakers_official,
    "two_violations": _two_violations,
    "olx_only": _olx_only,
    "concentration": _concentration,
}


# --- snapshot ---------------------------------------------------------------


def _benchmark(candidate: Any) -> float | None:
    """The phone index under either name (``kimovil_index`` became ``benchmark_index``)."""
    value = getattr(candidate, "benchmark_index", None)
    return value if value is not None else getattr(candidate, "kimovil_index", None)


def _snapshot(result: Any) -> dict[str, Any]:
    rec = result.recommendation
    ranked = sorted(
        rec.ranked,
        key=lambda s: (-round(s.total_score, 2), s.candidate.source, s.candidate.source_product_id),
    )
    return {
        "ranked": [
            {
                "id": f"{s.candidate.source}:{s.candidate.source_product_id}",
                "total": s.total_score,
                "confidence": s.confidence,
                "axes": [[a.name, a.score, a.max_score, a.explanation] for a in s.axes],
                "benchmark": _benchmark(s.candidate),
                "specs": s.candidate.specs,
            }
            for s in ranked
        ],
        "best": rec.best.candidate.source_product_id,
        "roles": {role: s.candidate.source_product_id for role, s in rec.alternatives.items()},
        "rejected_count": rec.rejected_count,
        "rejected_reasons": rec.rejected_reasons,
        "best_pros": rec.best.pros,
        "best_cons": rec.best.cons,
        "used_listings": [u.model_dump(mode="json") for u in result.used_listings],
        "ingredients_unchecked": result.ingredients_unchecked,
        "knowledge_missing": result.knowledge_missing,
    }


def _error_snapshot(exc: Exception) -> dict[str, Any]:
    snap: dict[str, Any] = {"error": type(exc).__name__, "message": str(exc)}
    for name in ("substances", "checked", "rejected_reasons"):
        if hasattr(exc, name):
            snap[name] = getattr(exc, name)
    if hasattr(exc, "alternatives"):
        snap["alternatives"] = [c.source_product_id for c in exc.alternatives]
    if hasattr(exc, "used_listings"):
        snap["used_listings"] = [u.model_dump(mode="json") for u in exc.used_listings]
    return snap


async def observe(
    name: str, use_llm: bool, fake: _FakeJev, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    try:
        result = await SCENARIOS[name](use_llm, fake, monkeypatch)
    except (NoNewPick, NoSubstanceMatch) as exc:
        return _error_snapshot(exc)
    return json.loads(json.dumps(_snapshot(result), ensure_ascii=False))


def _assert_close(actual: Any, expected: Any, path: str = "") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert actual.keys() == expected.keys(), path
        for key in expected:
            _assert_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), (path, actual, expected)
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            _assert_close(a, e, f"{path}[{i}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, abs=_SCORE_TOLERANCE), path
    else:
        assert actual == expected, path


@pytest.mark.parametrize("use_llm", [True, False], ids=["llm", "no-llm"])
@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_ranking_unchanged(
    name: str, use_llm: bool, fake_jev: _FakeJev, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    key = f"{name}[{'llm' if use_llm else 'no-llm'}]"

    actual = await observe(name, use_llm, fake_jev, monkeypatch)

    _assert_close(actual, expected[key], key)
