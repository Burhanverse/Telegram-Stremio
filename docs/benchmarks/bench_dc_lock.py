import asyncio
import time

# Simulate DC Session Initialization with and without double-checked lock
class MockClient:
    def __init__(self, idx):
        self.idx = idx
        self.media_sessions = {}

# Without double checked lock (Upstream base behavior during concurrent first requests to a DC)
async def upstream_concurrent_init(client: MockClient, dc: int, num_concurrent: int):
    sessions_created = 0
    async def get_session():
        nonlocal sessions_created
        if dc in client.media_sessions:
            return client.media_sessions[dc]
        # Simulate network latency of Auth.create + session.start + export/import auth
        await asyncio.sleep(0.02)
        sessions_created += 1
        session = f"session_dc_{dc}_{sessions_created}"
        client.media_sessions[dc] = session
        return session

    tasks = [asyncio.create_task(get_session()) for _ in range(num_concurrent)]
    results = await asyncio.gather(*tasks)
    return sessions_created, len(set(results))

# With Fork's double-checked per-DC lock
_dc_locks = {}
async def fork_concurrent_init(client: MockClient, dc: int, num_concurrent: int):
    sessions_created = 0
    async def get_session():
        nonlocal sessions_created
        if dc in client.media_sessions:
            return client.media_sessions[dc]
        lock_key = (client.idx, dc)
        if lock_key not in _dc_locks:
            _dc_locks[lock_key] = asyncio.Lock()
        async with _dc_locks[lock_key]:
            if dc in client.media_sessions:
                return client.media_sessions[dc]
            # Simulate network latency
            await asyncio.sleep(0.02)
            sessions_created += 1
            session = f"session_dc_{dc}_{sessions_created}"
            client.media_sessions[dc] = session
            return session

    tasks = [asyncio.create_task(get_session()) for _ in range(num_concurrent)]
    results = await asyncio.gather(*tasks)
    return sessions_created, len(set(results))

async def main():
    print("--- BENCHMARK 5: Concurrent DC Session Initialization (custom_dl.py) ---")
    c_up = MockClient(1)
    up_created, up_unique = await upstream_concurrent_init(c_up, 2, 10)
    print(f"Upstream without per-DC lock (10 concurrent requests to uninitialized DC):")
    print(f"  Sessions created: {up_created} | Duplicate redundant handshakes: {up_created - 1}")

    c_fork = MockClient(2)
    fork_created, fork_unique = await fork_concurrent_init(c_fork, 2, 10)
    print(f"Fork with double-checked per-DC lock (10 concurrent requests to uninitialized DC):")
    print(f"  Sessions created: {fork_created} | Duplicate redundant handshakes: {fork_created - 1}")

if __name__ == "__main__":
    asyncio.run(main())
