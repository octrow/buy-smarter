"""Source-failure logs must carry the exception type and a traceback."""

import logging

from buysmarter.services.recommend import _safe


async def test_source_failure_log_has_exception_type_and_traceback(caplog):
    async def boom():
        raise TimeoutError()  # empty str(exc) — the type is the only signal

    with caplog.at_level(logging.INFO, logger="buysmarter.services.recommend"):
        result = await _safe("kaspi", boom())

    assert result is None
    [record] = [r for r in caplog.records if "failed, skipping" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "TimeoutError" in record.getMessage()
    assert record.exc_info is not None
    assert record.exc_info[0] is TimeoutError
