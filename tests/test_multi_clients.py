import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from pyrogram.errors import FloodWait

import Backend.pyrofork.clients as clients_mod
from Backend.pyrofork.bot import StreamBot, client_dc_map, multi_clients, work_loads
from Backend.pyrofork.clients import client_tokens
from Backend.helper.settings_manager import SettingsManager


@pytest.fixture(autouse=True)
def clean_client_registries():
    """Reset multi_clients, work_loads, client_dc_map, and client_tokens before and after each test."""
    multi_clients.clear()
    work_loads.clear()
    client_dc_map.clear()
    client_tokens.clear()
    yield
    multi_clients.clear()
    work_loads.clear()
    client_dc_map.clear()
    client_tokens.clear()


@pytest.mark.asyncio
async def test_start_client_handles_floodwait(monkeypatch):
    """Verify that start_client() explicitly catches FloodWait, logs the wait time, and returns None."""
    class FakeFloodWait(FloodWait):
        def __init__(self, value: int):
            self.value = value

    mock_client_inst = MagicMock()
    mock_client_inst.start = AsyncMock(side_effect=FakeFloodWait(value=420))

    with patch("Backend.pyrofork.clients.Client", return_value=mock_client_inst):
        result = await clients_mod.start_client(1, "fake_token_1")

    assert result is None
    assert 1 not in multi_clients
    assert 1 not in work_loads


@pytest.mark.asyncio
async def test_start_client_handles_generic_exception(monkeypatch):
    """Verify that start_client() catches generic exceptions and returns None without raising."""
    mock_client_inst = MagicMock()
    mock_client_inst.start = AsyncMock(side_effect=RuntimeError("connection failed"))

    with patch("Backend.pyrofork.clients.Client", return_value=mock_client_inst):
        result = await clients_mod.start_client(2, "fake_token_2")

    assert result is None
    assert 2 not in multi_clients


@pytest.mark.asyncio
async def test_initialize_clients_partial_failure(monkeypatch):
    """Verify initialize_clients() filters out None before unpacking when some clients fail

    with FloodWait or generic exceptions.
    """
    mock_settings = MagicMock()
    mock_settings.multi_tokens = ["token1", "token2", "token3", "token4"]
    monkeypatch.setattr(SettingsManager, "current", MagicMock(return_value=mock_settings))

    # Fast-forward sleeps
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    # Mock StreamBot DC
    monkeypatch.setattr(StreamBot.storage, "dc_id", AsyncMock(return_value=1))

    mock_c1 = MagicMock()
    mock_c4 = MagicMock()

    class FakeFloodWait(FloodWait):
        def __init__(self, value: int):
            self.value = value

    async def mock_start_client(cid: int, token: str):
        if cid == 1:
            return cid, mock_c1
        elif cid == 2:
            return None  # Generic failure
        elif cid == 3:
            # Emulate FloodWait caught inside start_client
            return None
        elif cid == 4:
            return cid, mock_c4

    monkeypatch.setattr(clients_mod, "start_client", mock_start_client)

    await clients_mod.initialize_clients()

    # StreamBot (0), Client 1, and Client 4 must be registered
    assert multi_clients[0] is StreamBot
    assert multi_clients[1] is mock_c1
    assert multi_clients[4] is mock_c4
    assert 2 not in multi_clients
    assert 3 not in multi_clients
    assert len(multi_clients) == 3
    assert clients_mod.client_tokens == {1: "token1", 4: "token4"}


@pytest.mark.asyncio
async def test_initialize_clients_all_secondaries_fail_fallback_to_streambot(monkeypatch):
    """Verify that when ALL secondary bots fail, startup gracefully falls back to StreamBot alone."""
    mock_settings = MagicMock()
    mock_settings.multi_tokens = ["bad_tok1", "bad_tok2"]
    monkeypatch.setattr(SettingsManager, "current", MagicMock(return_value=mock_settings))

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(StreamBot.storage, "dc_id", AsyncMock(return_value=2))

    # All secondary clients return None
    monkeypatch.setattr(clients_mod, "start_client", AsyncMock(return_value=None))

    await clients_mod.initialize_clients()

    assert len(multi_clients) == 1
    assert multi_clients[0] is StreamBot
    assert len(clients_mod.client_tokens) == 0


@pytest.mark.asyncio
async def test_start_clients_staggered_concurrency_and_delay(monkeypatch):
    """Verify start_clients_staggered respects concurrency limits and inter-batch delay."""
    sleep_calls = []

    async def mock_sleep(d):
        sleep_calls.append(d)

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    active_count = 0
    max_active = 0

    async def mock_start(cid, token):
        nonlocal active_count, max_active
        active_count += 1
        if active_count > max_active:
            max_active = active_count
        await asyncio.sleep(0.01)
        active_count -= 1
        return cid, MagicMock()

    monkeypatch.setattr(clients_mod, "start_client", mock_start)

    tokens = {i: f"token_{i}" for i in range(1, 8)}  # 7 clients
    # With concurrency=3: batches of 3, 3, 1 -> 2 delays between batches
    results = await clients_mod.start_clients_staggered(tokens, concurrency=3, delay=0.25)

    assert len(results) == 7
    assert max_active <= 3
    batch_delays = [s for s in sleep_calls if s == 0.25]
    assert len(batch_delays) == 2  # Delay after batch 1 and batch 2


@pytest.mark.asyncio
async def test_reload_multi_token_clients_partial_failure(monkeypatch):
    """Verify reload_multi_token_clients() filters out None before unpacking on partial failure."""
    mock_settings = MagicMock()
    mock_settings.multi_tokens = ["tok_new_1", "tok_new_2"]
    monkeypatch.setattr(SettingsManager, "current", MagicMock(return_value=mock_settings))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    # Pre-populate StreamBot (client 0)
    multi_clients[0] = StreamBot

    mock_c1 = MagicMock()

    async def mock_start_client(cid: int, token: str):
        if cid == 1:
            return cid, mock_c1
        return None

    monkeypatch.setattr(clients_mod, "start_client", mock_start_client)

    result = await clients_mod.reload_multi_token_clients()

    assert multi_clients[1] is mock_c1
    assert 2 not in multi_clients
    assert result["total_clients"] == 2
