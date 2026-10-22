from market_sources.kimovil.parse import KimovilPhone

from buysmarter.ranking.models import Candidate
from buysmarter.services.phone_specs import (
    KimovilCache,
    enrich_with_kimovil,
    russian_specs,
)

S23 = KimovilPhone(
    full_name="Samsung Galaxy S23 Ultra",
    slug="s23u",
    url="https://www.kimovil.com/en/where-to-buy-s23u",
    ki=84.0,
    specs={
        "Type": "Super AMOLED Dynamic AMOLED 2X",
        "Others": "Hole-punch Notch Refresh rate 120 Hz",
        "Model": "Qualcomm Snapdragon 8 Gen2 ( 3,36 GHz)",
        "Score": "1.521.862 • Antutu v10",
        "Capacity": "256 GB",
        "Battery capacity": "5000 mAhSee more details",
        "Fast charge": "Yes , 45.0W",
        "Resistance certificates (dust, water)": "IP68",
        "DxOMark": "140",
    },
)


class FakeKimovil:
    def __init__(self, phones: dict[str, KimovilPhone | None]) -> None:
        self.phones = phones
        self.lookups: list[str] = []

    async def lookup(self, title: str) -> KimovilPhone | None:
        self.lookups.append(title)
        return self.phones.get(title)

    async def aclose(self) -> None:
        pass


def _cand(pid: str, title: str, specs: dict[str, str] | None = None) -> Candidate:
    return Candidate(
        source="kaspi", source_product_id=pid, title=title, price=500_000, specs=specs or {}
    )


def test_russian_specs_keep_variant_independent_fields_in_marketplace_wording():
    assert russian_specs(S23) == {
        "Модель (Kimovil)": "Samsung Galaxy S23 Ultra",
        "Тип экрана": "Super AMOLED Dynamic AMOLED 2X",
        "Частота обновления экрана": "120 Гц",
        "Процессор": "Qualcomm Snapdragon 8 Gen2",
        "AnTuTu v10": "1521862",
        "Ёмкость аккумулятора": "5000 мАч",
        "Быстрая зарядка": "45 Вт",
        "Защита": "IP68",
        "DxOMark": "140",
    }


def test_russian_specs_skip_what_the_page_lacks():
    bare = S23.model_copy(update={"specs": {}})
    assert russian_specs(bare) == {"Модель (Kimovil)": "Samsung Galaxy S23 Ultra"}


async def test_enrich_sets_the_index_and_merges_specs_marketplace_first(tmp_path):
    source = FakeKimovil({"samsung galaxy s23 ultra": S23})
    phone = _cand("1", "Смартфон Samsung Galaxy S23 Ultra 12/256GB", {"Защита": "IP68 (Kaspi)"})
    variant = _cand("2", "Samsung Galaxy S23 Ultra 12/512GB черный")
    unknown = _cand("3", "Смартфон Nokia 3310")

    await enrich_with_kimovil(
        [phone, variant, unknown], source=source, cache=KimovilCache(tmp_path / "k.db")
    )

    assert phone.benchmark_index == variant.benchmark_index == 84.0
    assert phone.specs["Защита"] == "IP68 (Kaspi)"  # the marketplace's own value wins
    assert phone.specs["Частота обновления экрана"] == "120 Гц"
    assert unknown.benchmark_index is None and unknown.specs == {}
    assert sorted(source.lookups) == ["nokia 3310", "samsung galaxy s23 ultra"]  # one per model


async def test_hits_and_misses_are_cached_across_runs(tmp_path):
    cache = KimovilCache(tmp_path / "k.db")
    first = FakeKimovil({"samsung galaxy s23 ultra": S23})
    await enrich_with_kimovil(
        [_cand("1", "Samsung Galaxy S23 Ultra"), _cand("2", "Nokia 3310")],
        source=first,
        cache=cache,
    )

    second = FakeKimovil({})
    again = _cand("1", "Samsung Galaxy S23 Ultra")
    await enrich_with_kimovil([again, _cand("2", "Nokia 3310")], source=second, cache=cache)

    assert second.lookups == []
    assert again.benchmark_index == 84.0


async def test_a_failing_lookup_leaves_candidates_untouched(tmp_path):
    class Broken(FakeKimovil):
        async def lookup(self, title):
            raise RuntimeError("cloudflare")

    phone = _cand("1", "Samsung Galaxy S23 Ultra")
    await enrich_with_kimovil([phone], source=Broken({}), cache=KimovilCache(tmp_path / "k.db"))

    assert phone.benchmark_index is None and phone.specs == {}


async def test_lookups_are_capped_per_run(tmp_path, monkeypatch):
    from buysmarter.services import phone_specs

    monkeypatch.setattr(phone_specs, "MAX_LOOKUPS", 2)
    source = FakeKimovil({})
    pool = [_cand(str(i), f"Samsung Galaxy A{i}0") for i in range(1, 5)]
    await enrich_with_kimovil(pool, source=source, cache=KimovilCache(tmp_path / "k.db"))

    assert source.lookups == ["samsung galaxy a10", "samsung galaxy a20"]
