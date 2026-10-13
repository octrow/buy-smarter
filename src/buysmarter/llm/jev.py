"""Jev (TypeSafe System One) through the local OmniRoute gateway.

``POST /v1/systemone`` on OmniRoute proxies TypeSafe's native wire: a ``state``
plus typed questions (noul / choice / score) in, one typed answer per question
out. No generation, so no JSON parsing or retry — the answer shape is the API
contract. Key and URL follow the pylibs claude-cli convention (``OMNIROUTE_API_KEY``
from env or ``~/.omniroute/.env``, ``OMNIROUTE_URL``).
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from claude_cli.http_api import api_key

from buysmarter.llm.adapter import LlmConfigError, LlmError

logger = logging.getLogger(__name__)

# Pinned, not ``jev-latest``: thresholds are tuned against this version, and the
# alias moves on a new release (docs.typesafe.ai/models).
JEV_MODEL = "typesafe/jev-1.13.0"
_DEFAULT_URL = "http://localhost:20128"
_TIMEOUT_S = 30.0


async def system_one(state: object, questions: dict[str, dict]) -> dict[str, dict]:
    """Ask ``questions`` about ``state``; return the ``answers`` map (same keys).

    Raises ``LlmConfigError`` without an OmniRoute key, ``LlmError`` on any
    transport/status/shape failure — callers stay fail-open as with ``generate``.
    """
    key = api_key()
    if not key:
        raise LlmConfigError("no OMNIROUTE_API_KEY for Jev")
    started = time.monotonic()
    payload = await _post(key, {"model": JEV_MODEL, "state": state, "questions": questions})
    answers = payload["answers"]
    missing = questions.keys() - answers.keys()
    if missing:
        raise LlmError(f"Jev answers missing for {sorted(missing)}")
    # OmniRoute call_logs skip the systemone proxy, so this is the only trace.
    logger.info(
        "jev %s: %d questions, %s input tokens, %.2fs",
        payload.get("model", JEV_MODEL),
        len(questions),
        (payload.get("usage") or {}).get("input_tokens", "?"),
        time.monotonic() - started,
    )
    return answers


async def _post(key: str, body: dict) -> dict:
    """POST ``body`` to OmniRoute's systemone proxy; any failure → ``LlmError``."""
    url = os.environ.get("OMNIROUTE_URL", _DEFAULT_URL) + "/v1/systemone"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LlmError(f"Jev call failed: {exc!r}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise LlmError(f"Jev returned no answers map: {str(payload)[:200]}")
    return payload
