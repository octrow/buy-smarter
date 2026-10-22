"""Source-failure logs must carry the exception type and a traceback."""

import logging

import httpx

from buysmarter.services.collect import _safe


async def test_source_failure_log_has_exception_type_and_traceback(caplog):
    async def boom():
        raise TimeoutError()  # empty str(exc) — the type is the only signal

    with caplog.at_level(logging.INFO, logger="buysmarter.services.collect"):
        result = await _safe("kaspi", boom())

    assert result is None
    [record] = [r for r in caplog.records if "failed, skipping" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "TimeoutError" in record.getMessage()
    assert record.exc_info is not None
    assert record.exc_info[0] is TimeoutError


async def test_source_http_status_error_logs_one_line_without_traceback(caplog):
    request = httpx.Request("GET", "https://kaspi.kz/yml/product-view/pl/filters")
    response = httpx.Response(503, request=request)

    async def down():
        response.raise_for_status()

    with caplog.at_level(logging.INFO, logger="buysmarter.services.collect"):
        result = await _safe("kaspi", down())

    assert result is None
    [record] = [r for r in caplog.records if "failed, skipping" in r.getMessage()]
    assert record.getMessage() == "Source 'kaspi' failed, skipping it: site answered HTTP 503"
    assert not record.exc_info
