"""SQLAlchemy 2 typed ORM models for the persistence layer.

Products, seller offers, price-history snapshots, reviews, single-user
preferences, LLM-generated category schemas and style vectors. The LLM response cache
(``LlmCache``) lives in ``buysmarter.llm.models``.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from buysmarter.db.base import Base, JsonType, UTCDateTime, utcnow


class Product(Base):
    """A canonical product aggregated from a marketplace source."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("source", "source_product_id", name="uq_product_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    source_product_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    specs: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now(), onupdate=utcnow
    )

    offers: Mapped[list["Offer"]] = relationship(back_populates="product")
    reviews: Mapped[list["Review"]] = relationship(back_populates="product")
    snapshots: Mapped[list["PriceSnapshot"]] = relationship(back_populates="product")


class Offer(Base):
    """A single seller offer for a product (price in KZT)."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    merchant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merchant_name: Mapped[str] = mapped_column(String(256))
    price: Mapped[int] = mapped_column(Integer)
    delivery_info: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="offers")


class PriceSnapshot(Base):
    """Point-in-time price aggregate for a product (price-trend history)."""

    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    min_price: Mapped[int] = mapped_column(Integer)
    offer_count: Mapped[int] = mapped_column(Integer)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="snapshots")


class Review(Base):
    """A single user review for a product."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    author: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)

    product: Mapped["Product"] = relationship(back_populates="reviews")


class UserPreference(Base):
    """Single-user memory: one key/value row per preference."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JsonType)


class CategoryKnowledge(Base):
    """LLM-generated schema for a product category (keyed by category name)."""

    __tablename__ = "category_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now(), onupdate=utcnow
    )


class StyleVector(Base):
    """A clothing-style embedding: a card photo (``card``, keyed by its URL) or a profile item.

    ``vector`` is the model's float32 output as bytes; ``label`` is what the report names
    ("your photo «…»"). Written by ``buysmarter.style``.
    """

    __tablename__ = "style_vectors"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_style_vectors_kind_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(1024))
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now()
    )
