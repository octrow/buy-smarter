"""``llm.jev.system_one``: success is logged (OmniRoute call_logs skip systemone)."""

import logging

import httpx

from buysmarter.llm import jev

_REAL_CLIENT = httpx.AsyncClient


def _serve(monkeypatch, payload: dict) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(jev, "api_key", lambda: "k")
    monkeypatch.setattr(
        jev.httpx, "AsyncClient", lambda **kw: _REAL_CLIENT(transport=transport, **kw)
    )


async def test_success_logs_model_questions_and_tokens(monkeypatch, caplog) -> None:
    _serve(
        monkeypatch,
        {
            "model": "jev-1.13.0",
            "answers": {"a": {"noul": 0.9}, "b": {"noul": 0.1}},
            "usage": {"input_tokens": 295, "output_tokens": 20},
        },
    )
    caplog.set_level(logging.INFO, logger=jev.__name__)

    answers = await jev.system_one({"t": "x"}, {"a": {}, "b": {}})

    assert answers["a"]["noul"] == 0.9
    line = caplog.records[-1].getMessage()
    assert "jev-1.13.0" in line and "2 questions" in line and "295 input tokens" in line


async def test_missing_usage_still_returns_answers(monkeypatch) -> None:
    _serve(monkeypatch, {"answers": {"a": {"noul": 0.5}}})

    assert await jev.system_one({}, {"a": {}}) == {"a": {"noul": 0.5}}
