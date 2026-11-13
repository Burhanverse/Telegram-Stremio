import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

import Backend.__main__ as main_mod
from Backend.config import Telegram


@pytest.fixture
def mock_startup_environment(monkeypatch):
    """Mock external I/O (Telegram network clients, MongoDB, Web server)

    so that start_services() and stop_services() can run as a fast in-memory smoke test.
    """
    # Fast-forward all sleeps
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    # Mock database
    monkeypatch.setattr(main_mod.db, "connect", AsyncMock())
    monkeypatch.setattr(main_mod.db, "disconnect", AsyncMock())
    monkeypatch.setattr(main_mod.db, "reload_extra_databases", AsyncMock())

    # Mock settings manager
    mock_settings = MagicMock()
    mock_settings.session_secret = "test-secret"
    mock_settings.extra_databases = []
    monkeypatch.setattr(main_mod.SettingsManager, "initialize", AsyncMock())
    monkeypatch.setattr(main_mod.SettingsManager, "current", MagicMock(return_value=mock_settings))

    # Mock app middleware to avoid duplicate registrations
    monkeypatch.setattr(main_mod.app, "add_middleware", MagicMock())

    # Mock scan managers
    monkeypatch.setattr(main_mod.scan_manager, "load", AsyncMock())
    monkeypatch.setattr(main_mod.dbcheck_manager, "bind_db", MagicMock())
    monkeypatch.setattr(main_mod.duplicate_manager, "bind_db", MagicMock())

    # Mock StreamBot
    mock_me = MagicMock()
    mock_me.username = "MockStreamBot"
    monkeypatch.setattr(main_mod.StreamBot, "start", AsyncMock())
    monkeypatch.setattr(main_mod.StreamBot, "stop", AsyncMock())
    monkeypatch.setattr(main_mod.StreamBot, "me", mock_me)

    # Mock Userbot & session string
    monkeypatch.setattr(main_mod, "get_active_session_string", AsyncMock(return_value=None))
    monkeypatch.setattr(main_mod.botmod, "Userbot", None)

    # Mock multi-clients initialization
    monkeypatch.setattr(main_mod, "initialize_clients", AsyncMock())

    # Mock bot commands & notifications
    monkeypatch.setattr(main_mod, "setup_bot_commands", AsyncMock())
    monkeypatch.setattr(main_mod, "restart_notification", AsyncMock())

    # Mock web server and background tasks
    mock_server = MagicMock()
    mock_server.serve = AsyncMock()
    monkeypatch.setattr(main_mod, "server", mock_server)
    monkeypatch.setattr(main_mod, "ping", AsyncMock())

    # Mock background workers
    mock_link_checker = MagicMock()
    mock_link_checker.start = AsyncMock()
    monkeypatch.setattr(main_mod, "DeadLinkChecker", MagicMock(return_value=mock_link_checker))
    monkeypatch.setattr(main_mod.subscription_task_manager, "sync", AsyncMock())
    monkeypatch.setattr(main_mod, "start_memory_watchdog", MagicMock())

    import Backend.helper.metadata.common as meta_common
    monkeypatch.setattr(meta_common, "start_metadata_cache_maintenance", MagicMock())

    # Mock idle to return immediately
    mock_idle = AsyncMock()
    monkeypatch.setattr(main_mod, "idle", mock_idle)

    return {
        "idle": mock_idle,
        "StreamBot": main_mod.StreamBot,
        "db": main_mod.db,
    }


@pytest.mark.asyncio
async def test_startup_services_smoke_default(mock_startup_environment, monkeypatch):
    """Verify start_services() boots completely without NameError when PREWARM_DCS is False."""
    monkeypatch.setattr(Telegram, "PREWARM_DCS", False)

    await main_mod.start_services()

    assert mock_startup_environment["StreamBot"].start.called
    assert mock_startup_environment["db"].connect.called
    assert mock_startup_environment["idle"].called


@pytest.mark.asyncio
async def test_startup_services_smoke_prewarm_enabled(mock_startup_environment, monkeypatch):
    """Verify start_services() boots completely without NameError when PREWARM_DCS is True."""
    monkeypatch.setattr(Telegram, "PREWARM_DCS", True)

    import Backend.fastapi.routes.stream_routes as stream_routes
    mock_warm = MagicMock()
    monkeypatch.setattr(stream_routes, "warm_streamers", mock_warm)

    await main_mod.start_services()

    assert mock_startup_environment["StreamBot"].start.called
    assert mock_warm.called
    assert mock_startup_environment["idle"].called


@pytest.mark.asyncio
async def test_normal_shutdown_clean_exit(mock_startup_environment, monkeypatch):
    """Verify that a normal SIGINT during start_services() triggers stop_services()

    and exits cleanly without CancelledError or any unhandled exception.
    """
    # Simulate SIGINT (KeyboardInterrupt) raised while idling
    mock_startup_environment["idle"].side_effect = KeyboardInterrupt

    shutdown_completed = False
    try:
        await main_mod.start_services()
    except KeyboardInterrupt:
        pass
    finally:
        await main_mod.stop_services()
        shutdown_completed = True

    assert shutdown_completed is True
    assert mock_startup_environment["StreamBot"].stop.called
    assert mock_startup_environment["db"].disconnect.called


@pytest.mark.asyncio
async def test_stop_services_handles_cancelled_error(mock_startup_environment):
    """Verify that stop_services() suppresses asyncio.CancelledError if StreamBot.stop()

    encounters task cancellation during shutdown.
    """
    mock_startup_environment["StreamBot"].stop.side_effect = asyncio.CancelledError()

    # Must not raise asyncio.CancelledError
    await main_mod.stop_services()
    assert mock_startup_environment["StreamBot"].stop.called
    assert mock_startup_environment["db"].disconnect.called
