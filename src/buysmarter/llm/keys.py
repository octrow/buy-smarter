"""User-supplied LLM API keys in the OS credential store (ADR 0004); the only keyring importer.

One key per provider under service ``buysmarter``, plus the chosen provider under ``_provider``.
There is no plaintext fallback: without a usable keyring, setting a key fails and the user is
pointed to the provider's environment variable.
"""

import functools
import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

logger = logging.getLogger(__name__)

SERVICE = "buysmarter"
_CHOSEN = "_provider"
# Provider -> its Settings field; the env var is the field upper-cased (OPENAI_API_KEY, ...).
KEY_FIELDS = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "gemini": "gemini_api_key",
    "deepseek": "deepseek_api_key",
}


class KeyStoreError(Exception):
    """The OS credential store cannot hold the key; the message is safe to show the user."""


def env_var(provider: str) -> str:
    return KEY_FIELDS[_known(provider)].upper()


def _known(provider: str) -> str:
    if provider not in KEY_FIELDS:
        raise ValueError(f"Unknown provider {provider!r}; expected one of {', '.join(KEY_FIELDS)}")
    return provider


def _read(username: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, username)
    except KeyringError as exc:
        logger.warning("OS keyring unreadable (%s); no stored LLM key", type(exc).__name__)
        return None


@functools.cache
def stored() -> tuple[str, str] | None:
    """(chosen provider, its key), read once per process: a keyring read may block on D-Bus.

    ``set_key``/``clear_key`` drop the cached value.
    """
    chosen = chosen_provider()
    key = get_key(chosen) if chosen else None
    return (chosen, key) if chosen and key else None


def set_key(provider: str, key: str) -> None:
    """Store ``key`` for ``provider`` and make it the chosen provider."""
    _known(provider)
    stored.cache_clear()
    if not key.strip():
        raise ValueError("The key is empty")
    try:
        keyring.set_password(SERVICE, provider, key.strip())
        keyring.set_password(SERVICE, _CHOSEN, provider)
    except KeyringError as exc:
        raise KeyStoreError(
            f"No usable OS credential store ({type(exc).__name__}). "
            f"Set the {env_var(provider)} environment variable instead."
        ) from exc


def get_key(provider: str) -> str | None:
    return _read(_known(provider))


def chosen_provider() -> str | None:
    provider = _read(_CHOSEN)
    return provider if provider in KEY_FIELDS else None


def clear_key(provider: str) -> None:
    """Remove ``provider``'s key; it stops being the chosen provider. Missing keys are fine."""
    stored.cache_clear()
    chosen = chosen_provider()
    usernames = [_known(provider), *([_CHOSEN] if chosen == provider else [])]
    for username in usernames:
        try:
            keyring.delete_password(SERVICE, username)
        except PasswordDeleteError:
            pass  # nothing stored under it
        except KeyringError as exc:
            raise KeyStoreError(f"Could not clear the key: {type(exc).__name__}") from exc
