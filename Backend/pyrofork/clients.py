import asyncio
import logging
from typing import Optional, Tuple

from pyrogram import Client
from pyrogram.errors import FloodWait

from Backend.config import Telegram
from Backend.helper.custom_dl import close_streamer_for_client
from Backend.helper.memory import spawn
from Backend.helper.settings_manager import SettingsManager
from Backend.logger import LOGGER
from Backend.pyrofork.bot import StreamBot, client_dc_map, multi_clients, work_loads

logger = logging.getLogger(__name__)

class _TCPConnectionDropFilter(logging.Filter):
    """
    Demote expected socket drops (connection reset by Telegram server during idle)
    from ERROR to DEBUG to avoid noisy and alarming log spam.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR and "Send failed:" in record.getMessage():
            msg = record.getMessage()
            if any(err in msg for err in ("ConnectionResetError", "Connection lost", "BrokenPipeError", "ConnectionAbortedError")):
                return False
        return True


logging.getLogger("pyrogram.connection.transport.tcp.tcp").addFilter(_TCPConnectionDropFilter())

client_tokens: dict[int, str] = {}


#----- Read the configured multi-client tokens from settings
class TokenParser:
    @staticmethod
    def parse_from_settings() -> dict[int, str]:
        tokens = SettingsManager.current().multi_tokens
        return {i + 1: tok.strip() for i, tok in enumerate(tokens) if tok and tok.strip()}


#----- Start a single bot client and register its DC/workload
async def start_client(client_id: int, token: str) -> Optional[Tuple[int, Client]]:
    try:
        LOGGER.info(f"Starting - Bot Client {client_id}")
        client = await Client(
            name=str(client_id),
            api_id=Telegram.API_ID,
            api_hash=Telegram.API_HASH,
            bot_token=token,
            sleep_threshold=100,
            no_updates=True,
            in_memory=True
        ).start()

        try:
            client_dc = await client.storage.dc_id()
            client_dc_map[client_id] = client_dc
            LOGGER.info(f"Client {client_id} connected to DC {client_dc}")
        except Exception as e:
            LOGGER.warning(f"Could not get DC for Client {client_id}: {e}")
            client_dc_map[client_id] = None

        work_loads[client_id] = 0
        return client_id, client
    except FloodWait as e:
        wait_s = getattr(e, "value", e)
        LOGGER.warning(
            f"Client {client_id} hit Telegram FloodWait: please wait {wait_s}s before connecting. Skipping this client."
        )
        return None
    except Exception as e:
        LOGGER.error(f"Failed to start Client - {client_id} Error: {e}", exc_info=True)
        return None


#----- Stop a client and purge all of its registry entries
async def stop_client(client_id: int) -> None:
    client = multi_clients.pop(client_id, None)
    work_loads.pop(client_id, None)
    client_dc_map.pop(client_id, None)
    client_tokens.pop(client_id, None)
    await close_streamer_for_client(client, client_id)

    if client:
        try:
            await client.stop()
            LOGGER.info(f"Stopped Bot Client {client_id}")
        except Exception as e:
            LOGGER.warning(f"Error stopping Client {client_id}: {e}")


#----- Start a batch of clients in a staggered fashion with concurrency limits
async def start_clients_staggered(
    tokens: dict[int, str],
    concurrency: Optional[int] = None,
    delay: Optional[float] = None,
) -> list[Optional[Tuple[int, Client]]]:
    if not tokens:
        return []

    c_limit = max(1, concurrency if concurrency is not None else getattr(Telegram, "CLIENT_INIT_CONCURRENCY", 3) or 3)
    c_delay = max(0.0, delay if delay is not None else (getattr(Telegram, "CLIENT_INIT_DELAY", 0.5) if getattr(Telegram, "CLIENT_INIT_DELAY", 0.5) is not None else 0.5))
    sem = asyncio.Semaphore(c_limit)

    async def _guarded_start(cid: int, tok: str) -> Optional[Tuple[int, Client]]:
        async with sem:
            return await start_client(cid, tok)

    items = list(tokens.items())
    results: list[Optional[Tuple[int, Client]]] = []

    for i in range(0, len(items), c_limit):
        batch = items[i : i + c_limit]
        tasks = [spawn(_guarded_start(cid, tok), name=f"start-client-{cid}") for cid, tok in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        if i + c_limit < len(items) and c_delay > 0:
            await asyncio.sleep(c_delay)

    return results


#----- Bring up the main client plus every configured extra client
async def initialize_clients() -> None:
    multi_clients[0], work_loads[0] = StreamBot, 0

    try:
        main_dc = await StreamBot.storage.dc_id()
        client_dc_map[0] = main_dc
        LOGGER.info(f"Main StreamBot connected to DC {main_dc}")
    except Exception as e:
        LOGGER.warning(f"Could not get DC for StreamBot: {e}")
        client_dc_map[0] = None

    all_tokens = TokenParser.parse_from_settings()
    if not all_tokens:
        LOGGER.info("No additional Bot Clients found, Using default client")
        return

    results = await start_clients_staggered(all_tokens)

    started = {res[0]: res[1] for res in results if res}
    multi_clients.update(started)
    for client_id, token in all_tokens.items():
        if client_id in started:
            client_tokens[client_id] = token

    if len(multi_clients) != 1:
        LOGGER.info(f"Multi-Client Mode Enabled with {len(multi_clients)} clients")
    else:
        LOGGER.info("No additional clients were initialized, using default client")


#----- Reconcile running clients with the current token settings
async def reload_multi_token_clients() -> dict:
    new_tokens = TokenParser.parse_from_settings()

    old_ids = set(client_tokens.keys())
    new_ids = set(new_tokens.keys())

    to_stop = [cid for cid in old_ids if cid not in new_ids or client_tokens.get(cid) != new_tokens.get(cid)]
    for cid in to_stop:
        await stop_client(cid)

    to_start = {cid: tok for cid, tok in new_tokens.items() if cid not in old_ids or client_tokens.get(cid) != tok}

    if to_start:
        results = await start_clients_staggered(to_start)
        started = {res[0]: res[1] for res in results if res}
        multi_clients.update(started)
        for cid, tok in to_start.items():
            if cid in started:
                client_tokens[cid] = tok

    LOGGER.info(
        f"Multi-token reload complete — {len(to_stop)} stopped, "
        f"{len(to_start)} (re)started, {len(multi_clients)} total clients active."
    )
    return {
        "stopped": len(to_stop),
        "started": len(to_start),
        "total_clients": len(multi_clients),
    }
