"""Seller questions (CONTEXT.md "Seller question"): a ready message to a Chinese seller.

Code, not a model: which topics an offer leaves open is a rule (``seller_topics``), the text is
a template (``seller_message``). The core only marks the topics; the user's body measurements
(CONTEXT.md "Body measurements") are filled in by the terminal and the app from the local
store, so a report file, MCP and the HTTP API never carry them.
"""

from enum import StrEnum

from pydantic import BaseModel

from buysmarter.ranking.authenticity import Authenticity, authenticity
from buysmarter.ranking.landed_cost import MarketRow

# Offers priced in yuan come from Chinese sellers: the only ones a question is written for.
CHINESE_CURRENCY = "CNY"
_FOREIGN_ROUTES = frozenset({"cargo", "direct"})
BLANK = "___"


class Topic(StrEnum):
    """What the offer's page does not settle."""

    SIZE = "размер"
    WEIGHT = "вес"
    AUTHENTICITY = "подлинность"


class Body(BaseModel, frozen=True):
    """The user's body measurements; ``None`` is a measurement not given."""

    height_cm: int | None = None
    weight_kg: int | None = None
    chest_cm: int | None = None
    waist_cm: int | None = None
    inseam_cm: int | None = None


def seller_topics(row: MarketRow) -> tuple[Topic, ...]:
    """The topics to ask a Chinese seller about; ``()`` for any other offer.

    The size is always asked: a Chinese size runs small and no page gives us its chart in cm.
    """
    if row.offer.candidate.currency != CHINESE_CURRENCY:
        return ()
    topics = [Topic.SIZE]
    if row.cost.weight_estimated and row.cost.route.key in _FOREIGN_ROUTES:
        topics.append(Topic.WEIGHT)
    if authenticity(row.offer) is not Authenticity.CERTAIN:
        topics.append(Topic.AUTHENTICITY)
    return tuple(topics)


# (field, Chinese name, Chinese unit, English name, English unit)
_MEASURES = (
    ("height_cm", "身高", "厘米", "height", "cm"),
    ("weight_kg", "体重", "公斤", "weight", "kg"),
    ("chest_cm", "胸围", "厘米", "chest", "cm"),
    ("waist_cm", "腰围", "厘米", "waist", "cm"),
    ("inseam_cm", "内长", "厘米", "inside leg", "cm"),
)


def _measures(body: Body | None) -> tuple[str, str]:
    """The measurements in Chinese and English; all blanks when none is given."""
    given = body.model_dump() if body is not None else {}
    if not any(given.values()):
        given = {field: BLANK for field, *_ in _MEASURES}
    zh = "，".join(f"{name}{given[f]}{unit}" for f, name, unit, _, _ in _MEASURES if given.get(f))
    en = ", ".join(f"{name} {given[f]} {unit}" for f, _, _, name, unit in _MEASURES if given.get(f))
    return zh, en


def seller_message(topics: tuple[Topic, ...], body: Body | None) -> tuple[str, str]:
    """The message in Chinese and in English; ``body=None`` leaves the measurements blank."""
    zh, en = ["您好！"], ["Hello!"]
    if Topic.SIZE in topics:
        zh_body, en_body = _measures(body)
        zh.append(
            f"请发一下这件的尺码表（厘米：胸围、衣长、肩宽、袖长、腰围、裤长）。"
            f"我{zh_body}，请问穿哪个码合适？"
        )
        en.append(
            "Please send the size chart in cm (chest, length, shoulders, sleeve, waist, "
            f"trouser length). My {en_body}: which size fits me?"
        )
    if Topic.WEIGHT in topics:
        zh.append("请问含包装的重量是多少公斤，包装尺寸是多少厘米？")
        en.append("What is the parcel weight with packaging in kg, and its size in cm?")
    if Topic.AUTHENTICITY in topics:
        zh.append("请问是正品吗？能否提供品牌授权或进货凭证，以及吊牌和水洗标的实拍照片？")
        en.append(
            "Is it genuine? Please send proof (brand authorisation or a purchase invoice) "
            "and real photos of the tag and the care label."
        )
    zh.append("谢谢！")
    en.append("Thank you!")
    return "".join(zh), " ".join(en)
