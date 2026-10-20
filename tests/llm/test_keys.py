"""Key store: one key per provider and the chosen provider, in the OS keyring only."""

import keyring
import pytest
from keyring.backends.fail import Keyring as NoBackend

from buysmarter.llm import keys


def test_a_set_key_is_in_the_credential_store_under_buysmarter(fake_keyring):
    keys.set_key("openai", "sk-test-123")
    assert fake_keyring.secrets[("buysmarter", "openai")] == "sk-test-123"
    assert keys.get_key("openai") == "sk-test-123"


def test_setting_a_key_makes_its_provider_the_chosen_one():
    keys.set_key("gemini", "AIza-test")
    assert keys.chosen_provider() == "gemini"


def test_clearing_a_key_removes_it_and_unchooses_its_provider(fake_keyring):
    keys.set_key("openai", "sk-test-123")
    keys.clear_key("openai")
    assert keys.get_key("openai") is None
    assert keys.chosen_provider() is None
    assert fake_keyring.secrets == {}


def test_clearing_a_missing_key_is_a_no_op():
    keys.set_key("openai", "sk-test-123")
    keys.clear_key("deepseek")
    assert keys.chosen_provider() == "openai"


def test_an_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="mistral"):
        keys.set_key("mistral", "x")


def test_a_blank_key_is_rejected():
    with pytest.raises(ValueError):
        keys.set_key("openai", "   ")


def test_without_a_keyring_setting_fails_naming_the_env_var_and_writes_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    keyring.set_keyring(NoBackend())
    with pytest.raises(keys.KeyStoreError, match="OPENAI_API_KEY"):
        keys.set_key("openai", "sk-test-123")
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_the_stored_key_is_read_once_and_refreshed_by_set_and_clear(fake_keyring):
    keys.set_key("openai", "sk-1")
    assert keys.stored() == ("openai", "sk-1")
    fake_keyring.secrets.clear()  # an outside change is not re-read...
    assert keys.stored() == ("openai", "sk-1")
    keys.clear_key("openai")  # ...but our own writes drop the cached value
    assert keys.stored() is None


def test_without_a_keyring_reads_find_no_key():
    keyring.set_keyring(NoBackend())
    assert keys.get_key("openai") is None
    assert keys.chosen_provider() is None


def test_every_stored_provider_matches_an_adapter_provider_and_its_settings_field():
    from buysmarter.llm.adapter import PROVIDERS

    adapter_fields = {p.name: p.key_field for p in PROVIDERS.values()}
    assert adapter_fields == keys.KEY_FIELDS
