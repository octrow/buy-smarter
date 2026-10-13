from buysmarter.db.models import PriceSnapshot


def test_price_snapshot_has_nullable_review_stat_columns() -> None:
    columns = PriceSnapshot.__table__.columns

    assert columns["review_count"].nullable is True
    assert columns["rating"].nullable is True
