import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock
from pyrogram.types import Message
import Backend.pyrofork.plugins.start as start_plugin
import Backend.pyrofork.plugins.subscription as sub_plugin


def test_kurigram_message_reply_signature_rejects_quote():
    """Verify that Kurigram's Message.reply signature rejects 'quote' argument."""
    sig = inspect.signature(Message.reply)
    assert "quote" not in sig.parameters

    with pytest.raises(TypeError, match="unexpected keyword argument 'quote'"):
        sig.bind(MagicMock(), "Hello", quote=True)


@pytest.mark.asyncio
async def test_start_command_reply_arguments_compatible(monkeypatch):
    """Verify that the /start command handler calls reply_text with valid Kurigram arguments."""
    sig = inspect.signature(Message.reply)

    from Backend.config import Telegram
    monkeypatch.setattr(Telegram, "OWNER_ID", 12345)
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.from_user.id = 12345
    mock_message.from_user.first_name = "TestUser"
    mock_message.from_user.username = "testuser"
    mock_message.chat.id = 12345
    mock_message.sender_chat = None

    reply_calls = []

    async def mock_reply_text(*args, **kwargs):
        # Must bind to Kurigram's signature without TypeError
        sig.bind(mock_message, *args, **kwargs)
        reply_calls.append((args, kwargs))

    mock_message.reply_text = mock_reply_text

    # Mock DB token and settings
    mock_db = MagicMock()
    mock_db.add_api_token = AsyncMock(return_value={"token": "test-token"})
    mock_db.get_user = AsyncMock(return_value=None)
    mock_db.is_subscription_active = MagicMock(return_value=False)
    mock_db.get_subscription_plans = AsyncMock(return_value=[])

    monkeypatch.setattr(start_plugin, "db", mock_db)
    monkeypatch.setattr(start_plugin.SettingsManager, "current", MagicMock(return_value=MagicMock(subscription=False)))

    # Run free mode
    await start_plugin.send_start_message(mock_client, mock_message)
    assert len(reply_calls) == 1
    assert "quote" not in reply_calls[0][1]

    # Run subscription mode (no plans)
    reply_calls.clear()
    monkeypatch.setattr(start_plugin.SettingsManager, "current", MagicMock(return_value=MagicMock(subscription=True)))
    await start_plugin.send_start_message(mock_client, mock_message)
    assert len(reply_calls) == 1
    assert "quote" not in reply_calls[0][1]


@pytest.mark.asyncio
async def test_subscription_reply_arguments_compatible(monkeypatch):
    """Verify that subscription handlers call reply_text with valid Kurigram arguments."""
    sig = inspect.signature(Message.reply)

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.from_user.id = 99999
    mock_message.chat.id = 99999
    mock_message.sender_chat = None

    reply_calls = []

    async def mock_reply_text(*args, **kwargs):
        sig.bind(mock_message, *args, **kwargs)
        reply_calls.append((args, kwargs))

    mock_message.reply_text = mock_reply_text

    mock_db = MagicMock()
    mock_db.get_user = AsyncMock(return_value=None)  # No pending payment

    monkeypatch.setattr(sub_plugin, "db", mock_db)
    monkeypatch.setattr(sub_plugin.SettingsManager, "current", MagicMock(return_value=MagicMock(subscription=True)))

    await sub_plugin.handle_payment_screenshot(mock_client, mock_message)
    assert len(reply_calls) == 1
    assert "quote" not in reply_calls[0][1]
