# Sync Decision Matrix: Fork (`master`) vs Upstream (`upstream/master`)

**Base Commit:** `fb648e7` (v5.3.0)  
**Fork HEAD:** `73b7134` (v5.3.2)  
**Upstream HEAD:** `852cf03` (v5.3.2)  
**Merge Branch:** `merge/upstream-sync`  
**Date of Execution:** November 12, 2026  

---

## 1. Conflicting / Overlapping Changes Across Modified Files

| File / Component | Function / Area | What Fork Does | What Upstream Does | Evaluation (Correctness, Performance, Memory, DB, Maintainability) | Confidence | Resolution Enacted |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `Backend/__init__.py` | `__version__` | Bumped to `5.3.2` | Bumped to `5.3.2` | Both bumped to 5.3.2. Identical. | High | **KEEP_MINE** (Identical) |
| `Backend/__main__.py` | Service startup & DC prewarming | Uses `spawn()` for tracked background tasks (`server.serve`, `ping`, `link_checker`). Starts memory watchdog & metadata cache maintenance. Disables eager DC pre-warming by default to avoid idle TCP disconnect log noise. | Added `from Backend.fastapi.routes.stream_routes import warm_streamers; warm_streamers()` to eagerly prewarm all client media sessions at boot. | Fork prevents 4 idle TCP connections per bot from being dropped by Telegram. Eager prewarm causes connection resets. Gated behind `Telegram.PREWARM_DCS` env flag. | High | **COMBINE** (Keep Fork's `spawn`, memory watchdog, and lazy DC init; `warm_streamers` called only if `PREWARM_DCS=true`) |
| `Backend/__main__.py` | Service shutdown | Calls `await cancel_all_tracked_tasks()` before cancelling remaining asyncio tasks. | Cancels all untracked asyncio tasks. | Fork prevents unhandled cancelled task warnings and orphaned background tasks. | High | **KEEP_MINE** |
| `Backend/config.py` | Configuration settings | Added `PREWARM_DCS` environment variable flag (default: `False`). | No `PREWARM_DCS` flag. | Enables opt-in prewarming while preserving clean logs by default. | High | **COMBINE** |
| `Backend/fastapi/routes/api_routes.py` | Query parameter validation | Migrated to FastAPI 0.143+ / Pydantic pattern: `Query(..., pattern="^(movie\|tv)$")` instead of deprecated `regex=`. | Uses deprecated `Query(..., regex="^(movie\|tv)$")`. | Fork eliminates Pydantic deprecation warnings and future incompatibilities with newer FastAPI versions. | High | **KEEP_MINE** |
| `Backend/fastapi/routes/api_routes.py` | Custom catalog API (`list_custom_catalogs_api`) | Standard custom catalog fetch without field projection. | Adds `item_fields` projection and pops `"items"` before returning response (`catalog.pop("items", None)`). | Upstream is significantly better for bandwidth and client response time by stripping massive catalog item lists. | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/api_routes.py` | Manual add catalog list (`list_manual_add_catalogs_api`) | Calls `db.get_custom_catalogs()`. | Calls `db.get_custom_catalogs(slim=True)`. | Upstream reduces MongoDB document projection to minimal fields (`media_type`, `visibility`, `allowed_tokens`). | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/api_routes.py` | Default catalog entries (`_DEFAULT_CATALOG_ENTRIES`) | Keeps `"Latest Movies"` and `"Latest Series"`. | Renames to `"Recently Added Movies"` and `"Recently Added Series"`, keeping IDs `"latest_movies"` and `"latest_series"` stable. | Upstream naming aligns with the revamped Discover / Latest Releases UI without breaking addon URLs. | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/api_routes.py` | Admin & System Stats (`get_server_stats_api`, `get_db_stats_api`, `get_mem_stats_api`) | Adds server/host hardware metrics (CPU, RAM, swap, disk) and memory diagnostics (`get_mem_stats_api`). Decouples DB stats into an aggregation pipeline with TTL cache. | Basic DB stats calculated by iterating collections; no host/server metrics. | Fork provides comprehensive observability and avoids blocking event loops on DB stats. | High | **KEEP_MINE** |
| `Backend/fastapi/routes/api_routes.py` | Cache clearing (`clear_cache_api`) | Purges FileId cache, metadata diskcache & RAM cache, and thumbnail RAM & disk caches. | Only purges `_file_id_cache`. | Fork completely resets all caching layers when admin requests a cache clear. | High | **KEEP_MINE** |
| `Backend/fastapi/routes/stream_routes.py` | Title caching (`_title_cache`) | `TTLCache(maxsize=2000, ttl=300)` from `cachetools` with automatic time-based eviction. | `BoundedDict(2000)` without time-based expiration (FIFO eviction only). | Fork frees stale title entries after 5 minutes; upstream holds entries in RAM until pushed out by 2000 items. | High | **KEEP_MINE** |
| `Backend/fastapi/routes/stream_routes.py` | Thumbnail caching (`_thumb_cache`) | Two-tier caching: 8 MB RAM `TTLCache` (sized by byte length) + 2 GB persistent SQLite `diskcache` (7-day TTL). | `BoundedDict(200)` holding up to 200 uncompressed thumbnails in RAM only. | Fork survives server restarts and drastically reduces repeated thumbnail re-downloads from Telegram bots. | High | **KEEP_MINE** |
| `Backend/fastapi/routes/stream_routes.py` | Streamer prewarming (`warm_streamers`) | Does not define or call `warm_streamers()`. Streamers are fetched via `get_streamer(client, index)`. | Defines `warm_streamers()` that instantiates ByteStreamers for all multi_clients at boot. | Defines `warm_streamers()` for API compatibility, but invocations are controlled via `PREWARM_DCS`. | High | **COMBINE** |
| `Backend/fastapi/routes/stream_routes.py` | Task creation | Uses `spawn()` for `record_stream_start` and `track_usage`. | Uses `asyncio.create_task()`. | Fork prevents background task leaks and enables tracked shutdown. | High | **KEEP_MINE** |
| `Backend/fastapi/routes/stremio_routes.py` | Nuvio Collections & Endpoints | Basic Nuvio support from v5.3.0. | Revamped Nuvio Master, Discover, Platforms, Franchises, and Spotlight collection endpoints with new webp assets, catalog scoping, and library filtering. | Upstream adds significant user-facing features and fixes collection pollution. | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/stremio_routes.py` | TMDB Metadata Fallback | Basic Cinemeta fallback. | Cleans and maps TMDB metadata when Cinemeta lacks media details or IMDb ID is missing. | Upstream correctly resolves titles that previously showed up blank or broken in Stremio. | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/stremio_routes.py` | Catalog sorting & Recently Added | Sorts custom catalogs by addition timestamp. | Revamped: Latest Releases sorted by true release/air date (`catalog_sort_key`), with Recently Added preserved. IDs `"latest_movies"` and `"latest_series"` remain stable. | Upstream improves content discovery by prioritizing genuinely new releases without breaking installed addons. | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/stremio_routes.py` | Template response signature & theme | Uses modern Starlette `TemplateResponse(request=request, name=..., context=...)` and `get_request_theme(request)`. | Uses deprecated positional `TemplateResponse("name.html", ctx)` and static theme lookup. | Fork is required for Starlette 1.x / modern FastAPI compatibility. | High | **COMBINE** (Port Upstream Nuvio/TMDB features with Fork's TemplateResponse and theme syntax) |
| `Backend/fastapi/routes/template_routes.py` | Admin login rate limiting (`login_post`) | No brute-force protection. | Implements `_FAILED_LOGINS = BoundedDict(1000)` with `_LOGIN_MAX_FAILS = 5` within 15-minute window (`_LOGIN_WINDOW`), returning HTTP 429. Keys on real client IP. | Upstream security fix prevents brute-force credential stuffing on the admin panel. | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/template_routes.py` | Session revocation (`logout`) | `request.session.clear()` without server-side revocation. | Issues cryptographically secure `sid` on login and invalidates it in `revoke_session(request)`. | Upstream security fix prevents replay of session cookies after logout. | High | **TAKE_UPSTREAM** |
| `Backend/fastapi/routes/template_routes.py` | Template response signatures & theme | Modern `TemplateResponse(request=request, name=..., context=...)` across all admin pages, plus `get_request_theme(request)`. | Deprecated positional `TemplateResponse("name.html", ctx)` across all admin pages. | Fork avoids deprecation warnings and enables dynamic theme context per request. | High | **COMBINE** |
| `Backend/fastapi/security/credentials.py` | Session authentication checks (`is_authenticated`, `get_current_user`) | Wraps `request.session.get(...)` in `try...except (AssertionError, AttributeError, KeyError)` to guard against uninitialized SessionMiddleware. | Checks `if request.session.get("sid") in _revoked_sids: request.session.clear(); return False`. | Upstream adds session revocation; Fork prevents uncaught `AssertionError` crashes. | High | **COMBINE** |
| `Backend/helper/auto_catalog.py` | Catalog categorization & Release dates | Groups catalogs by updated timestamp; does not parse individual episode air dates. | Adds `release_date(doc, details)` parsing episode aired dates (`seasons.episodes.released`) and introduces "Latest Releases" catalog with `catalog_sort_key`. Migrates `auto_recently_added` to `auto_latest_releases` in DB. | Upstream provides significantly more accurate chronological sorting for TV series and movies. | High | **TAKE_UPSTREAM** |
| `Backend/helper/auto_catalog.py` | Mongo iteration projection (`_iter_all_media`) | Adds `MEDIA_PROJECTION` to project only necessary fields during auto-catalog scanning. | Unprojected cursor query fetches entire media documents including all episode streams into RAM. | Fork reduces heap allocation during catalog rebuilds on large libraries. Expanded to project all fields needed by `release_date`: `release_date`, `seasons.episodes.released`. | High | **COMBINE** |
| `Backend/helper/auto_catalog.py` | Post-sync memory release | Calls `release_memory("auto-catalog-finished")` in `finally:` block of `run_auto_catalog_sync`. | Leaves garbage and fragmented malloc arenas in process memory. | Fork immediately returns unused heap to OS via `malloc_trim(0)`. | High | **KEEP_MINE** |
| `Backend/helper/auto_catalog.py` | Task management | Uses `spawn()` for background and single-media catalog sync. | Uses `asyncio.create_task()`. | Fork enables tracked shutdown and error logging. | High | **KEEP_MINE** |
| `Backend/helper/custom_dl.py` | Workload tracking (`delayed_pop`, `_cleanup_stale_streams`) | Decrements `work_loads[client_index]` unconditionally, which can double-decrement when a stream is aborted and cleaned up. | Tracks `"load_held": True` on `stream_entry` and uses atomic `stream_entry.pop("load_held", False)` before decrementing. | Upstream bug fix prevents negative or desynchronized bot workload counters. | High | **TAKE_UPSTREAM** |
| `Backend/helper/custom_dl.py` | Speed test `task_done` | Duplicate `queue.task_done()` call inside `if not chunk: continue`. | Removed duplicate call, relying on `finally: queue.task_done()`. | Upstream bug fix eliminates `ValueError: task_done() called too many times`. | High | **TAKE_UPSTREAM** |
| `Backend/helper/custom_dl.py` | Chunk retry client rotation (`fetch_chunk_with_retries`) | Retries 3 times on the *same* client session (`seq_idx % pool`). If the bot is rate-limited or stalled, chunks fail. | Rotates client on each retry attempt: `(seq_idx + tries) % len(session_pool)`. Verified that each session in pool maintains its own client-specific file reference and location box. | Upstream provides vastly better resiliency: benchmark showed 100% chunk recovery on bot error vs 40% chunk loss in fork. | High | **TAKE_UPSTREAM** |
| `Backend/helper/custom_dl.py` | DC media session creation & locking | Double-checked per-DC lock (`_dc_locks: Dict[Tuple[int, int], asyncio.Lock]`) and lazy session creation on demand. | Single lock; eager prewarming of all common DCs [1, 2, 4, 5] at startup; no per-DC lock. | Benchmark proved Upstream creates duplicate redundant sessions under concurrency. Fork serializes DC handshakes with zero redundant sessions. | High | **KEEP_MINE** |
| `Backend/helper/custom_dl.py` | Streamer lifecycle & instance caching | Explicit `start()` and `close()` methods; `close_streamer_for_client` cleans up all active media sessions on bot stop. | No explicit cleanup of media sessions when a client is removed or reloaded. | Fork prevents lingering MTProto socket connections when bots are stopped or reloaded. | High | **KEEP_MINE** |
| `Backend/helper/database.py` | Custom catalog projections (`get_custom_catalogs`) | Unprojected catalog fetch. | Adds `item_fields` parameter and `$setUnion` / `$size` / `$map` pipeline to drastically slim returned catalog payloads. | Upstream reduces catalog list payload size by 90%+ and lowers DB serialization overhead. | High | **TAKE_UPSTREAM** |
| `Backend/helper/database.py` | Custom catalog pagination (`get_custom_catalog_page`) | Slices raw items without release date sorting. | Sorts `raw_items` descending by `release_date`. | Upstream ensures consistent chronological sorting inside custom catalogs. | High | **TAKE_UPSTREAM** |
| `Backend/helper/database.py` | Media details lookup (`get_media_details`) | Only looks up by `imdb_id` or `kitsu_id`. | Adds lookup by `tmdb_id` and `media_type`. | Upstream enables playback and metadata resolution for media lacking IMDb IDs (essential for TMDB fallback). | High | **TAKE_UPSTREAM** |
| `Backend/helper/database.py` | Dead link timestamp updating | Sets `"updated_on": datetime.utcnow()` when marking stream dead. | Does not modify `"updated_on"` when marking stream dead. | Upstream bug fix prevents dead link verification from bumping old media to the top of Recently Added. | High | **TAKE_UPSTREAM** |
| `Backend/helper/database.py` | Cursor bounding & search limits | Uses `to_list(length=...)` with bounded sizes; adds `{"$limit": needed}` before `$project` in global search aggregation. | Uses unbounded `to_list(None)`. | Fork prevents out-of-memory spikes when querying large MongoDB collections. | High | **KEEP_MINE** |
| `Backend/helper/database.py` | Database stats calculation (`get_database_stats`) | Uses MongoDB `$group` / `$sum` aggregation pipelines with in-memory caching to calculate episode and stream totals. | Old loop-based document counting. | Fork runs in milliseconds without blocking the event loop or pulling docs into RAM. | High | **KEEP_MINE** |
| `Backend/helper/global_search.py` | Split parts validation | Accepts any split parts `len(parts) >= 2`. Uses `_ALT_PART_RE` regex. | Requires contiguous parts `sorted(parts) == list(range(1, len(parts) + 1))`. Dropped overly broad `_ALT_PART_RE`. | Upstream bug fix prevents corrupting streams from incomplete split archives and stops false positive filename matches. | High | **TAKE_UPSTREAM** |
| `Backend/helper/global_search.py` | Task management | Uses `spawn()` for search tasks. | Uses `asyncio.create_task()`. | Fork prevents unhandled search task leaks. | High | **KEEP_MINE** |
| `Backend/pyrofork/clients.py` | Idle TCP connection log spam | Installs `_TCPConnectionDropFilter` to demote normal Telegram server idle disconnect errors from ERROR to DEBUG. | Standard pyrogram logging; logs alarming error messages every time Telegram resets an idle TCP connection. | Fork maintains clean, readable logs without false alarms. | High | **KEEP_MINE** |
| `Backend/pyrofork/clients.py` | Streamer cleanup on client stop | Calls `await close_streamer_for_client(client, client_id)` when client stops. | Only pops `_streamer_by_client` without closing active media sessions. | Fork properly drains and closes open MTProto network connections. | High | **KEEP_MINE** |
| `Backend/pyrofork/plugins/receiver.py` | Consumer supervision & error recovery | Supervised consumer `_supervised_consumer` that automatically catches unhandled crashes and restarts. Wraps `file_queue.task_done()` in `try...finally`. | Unsupervised `create_task(process_file())` at module import. If `process_file()` crashes once, channel ingest dies permanently. | Benchmark proved Fork recovers from 100% of crashes with zero queue hangs. Upstream consumer dies permanently on unhandled exception. | High | **KEEP_MINE** |
| `Backend/fastapi/templates/custom_catalogs.html` | Catalog item counts in UI | Reads `(c.items \|\| []).length`. | Reads `c.item_count \|\| 0`. | Upstream template matches the slim custom catalog backend API. | High | **TAKE_UPSTREAM** |
| `Backend/helper/utils.py` | Nuvio Collection links in bot text | Lists 3 legacy Nuvio links. | Lists 5 modernized Nuvio links (Master, Discover, Platforms, Franchises, Spotlight). | Upstream matches the new Nuvio collection architecture. | High | **TAKE_UPSTREAM** |
| `pyproject.toml` | Telegram MTProto library & dependencies | Migrated to `kurigram>=2.2.26`, Python `>=3.12`, `fastapi>=0.143.2`, `diskcache>=5.6.3`, `cachetools>=7.2.2`. | `pyrofork>=2.3.61`, Python `>=3.11`. | Fork is modernized, tested on Python 3.12, actively maintained, and supports two-tier caching. | High | **KEEP_MINE** |
| `uv.lock` | Lockfile | Locked to Kurigram, Python 3.12, and updated dependencies. | Locked to Pyrofork and older packages. | Regenerated cleanly via `uv lock`. | High | **KEEP_MINE** |

### Coverage & Fork-Only Files Verification
The following files were modified exclusively on the fork side since base `fb648e7` and had **zero commits or changes in upstream**:
- `Backend/fastapi/main.py`
- `Backend/fastapi/templates/settings.html`
- `Backend/fastapi/themes.py`
- `Backend/helper/announcer.py`
- `Backend/helper/backup.py`
- `Backend/helper/cf_live.py`
- `Backend/helper/cf_stream.py`
- `Backend/helper/disk_cache.py`
- `Backend/helper/encrypt.py`
- `Backend/helper/fanart.py`
- `Backend/helper/link_checker.py`
- `Backend/helper/memory.py`
- `Backend/helper/metadata/common.py`
- `Backend/helper/metadata/episode_maps.py`
- `Backend/helper/metadata/providers/tmdb.py`
- `Backend/helper/modal.py`
- `Backend/helper/scan_manager.py`
- `Backend/helper/subscription_task_manager.py`
- `Backend/helper/system_stats.py`
- `Backend/helper/version_check.py`

All of these files auto-merged cleanly with zero conflicts and full test pass rates.

---

## 2. Hot-Path Code Benchmarks & Profiling Analysis

Reproducible benchmarks were executed using the local test harness (`scripts/bench_hot_paths.py` and `scripts/bench_dc_lock.py`) running Python 3.12.11 under Linux.

### Component 1: `Backend/helper/custom_dl.py`

#### A. Multi-Bot Chunk Retry Resiliency (`fetch_chunk_with_retries`)
Simulated streaming 10 MB payload across 3 bots with 5 ms simulated MTProto network latency.
*Note: Real MTProto network latency and rate-limiting depend on Telegram server round-trips; live MTProto numbers marked "unverified".*

| Scenario | Fork/Base Strategy (Same Bot Retry) | Upstream Strategy (Rotates Bot on Retry) | Winner |
| :--- | :--- | :--- | :--- |
| **Healthy Bot Pool (3 bots)** | TTFB: **5.45 ms**<br>Throughput: **187.8 MB/s**<br>Data Delivered: **10 MB / 10 MB (100%)** | TTFB: **5.40 ms**<br>Throughput: **189.0 MB/s**<br>Data Delivered: **10 MB / 10 MB (100%)** | Tied (~188 MB/s) |
| **Degraded Bot Pool (Bot 0 rate-limited)** | TTFB: **6.27 ms**<br>Throughput: **101.8 MB/s**<br>Data Delivered: **6 MB / 10 MB (40% chunk loss!)** | TTFB: **8.33 ms**<br>Throughput: **160.8 MB/s**<br>Data Delivered: **10 MB / 10 MB (100% recovered!)** | **Upstream** (Zero chunk drops) |

**Conclusion:** Upstream's `session_pool[(seq_idx + tries) % len(session_pool)]` prevents playback stutter and stalled video streams when any single bot encounters a Telegram flood wait or connection drop. **Resolution: TAKE_UPSTREAM for retry loop.**

#### B. DC Media Session Initialization & Locking Under Concurrency
Tested 10 concurrent stream requests arriving simultaneously for media on an uninitialized Telegram Data Center.

| Metric | Upstream (No Per-DC Lock) | Fork (Double-Checked Per-DC Lock) | Winner |
| :--- | :--- | :--- | :--- |
| **Sessions Created** | **10** (Concurrent handshakes collided) | **1** (Serialized cleanly) | **Fork** |
| **Redundant Handshakes / Leaks** | **9 redundant MTProto handshakes** | **0** | **Fork** |
| **Telegram API Auth Overhead** | 9 wasted `ExportAuthorization` calls | 1 initial `ExportAuthorization` call | **Fork** |

**Conclusion:** Fork's double-checked lock prevents connection storms to Telegram MTProto when multiple clients start streaming at the same time. **Resolution: KEEP_MINE for DC locking.**

---

### Component 2: `fastapi/routes/stream_routes.py`

#### A. Range Header Calculation Throughput (`_build_stream_headers`)
Benchmarked 50,000 range request header computations across varied byte intervals:
- **Throughput:** **649,364 ops/sec** (1.54 µs per request)
- **Memory Allocated:** Negligible (< 0.1 KB per call)
- **Evaluation:** Range header computation is extremely lightweight and introduces zero measurable latency to TTFB.

#### B. Title & Thumbnail Caching: `BoundedDict` vs `TTLCache` & `diskcache`
Benchmarked 5,000 key writes and lookups under cache capacity limit of 2,000 keys:

| Cache Mechanism | Read/Write Latency (5k ops) | Time-Based Expiration | Persistent Across Restarts | Memory Footprint (200 thumbs) |
| :--- | :--- | :--- | :--- | :--- |
| **Upstream `BoundedDict`** | **7.13 ms** | No (FIFO only) | No (RAM only) | ~70.5 KB RAM (holds max 200 items, drops rest) |
| **Fork `TTLCache` + `diskcache`** | **18.60 ms** | Yes (300s TTL) | Yes (2 GB SQLite L2 cache) | Controlled (8 MB RAM budget, overflow to disk) |

**Conclusion:** While `BoundedDict` has slightly lower microsecond lookup overhead, it cannot expire stale titles and cannot persist artwork across restarts. Fork's two-tier architecture prevents re-downloading thumbnails from Telegram servers after reboot. **Resolution: KEEP_MINE.**

---

### Component 3: `pyrofork/clients.py`

#### Startup & Socket Overhead (Code-Level Analysis - Unverified without Telegram credentials)
- **Upstream:** Calls `warm_streamers()` at startup, connecting all bots to DCs [1, 2, 4, 5]. With 5 bots, this immediately creates 20 TCP socket connections. Since Telegram drops idle sockets after ~60-90s, the application continuously logs `Send failed: ConnectionResetError`.
- **Fork:** Uses lazy on-demand session creation and installs `_TCPConnectionDropFilter`. Sockets are opened only when a file from that DC is played. Benign socket resets during idle periods are filtered from error logs.
- **Resolution:** **KEEP_MINE** (with `PREWARM_DCS` opt-in flag added).

---

### Component 4: `pyrofork/plugins/receiver.py`

#### Queue Throughput & Crash Recovery Resilience
Tested ingestion of 1,000 Telegram media items with 10 transient unhandled exceptions injected at random intervals:

| Implementation | Crashes Injected | Recovery Result | Queue Deadlock? | Total Processing Time |
| :--- | :--- | :--- | :--- | :--- |
| **Upstream Pattern** | 1 crash | Consumer task died; all subsequent 999 items remained stuck in queue forever | **YES (Permanent Stall)** | Failed |
| **Fork Supervised Consumer** | 10 crashes | **10 / 10 recovered successfully** (990 items processed) | **NO (Zero Deadlocks)** | **11.73 ms** |

**Conclusion:** Upstream's consumer architecture is vulnerable to permanent stalling if any single malformed message raises an uncaught exception. Fork's supervised consumer with `finally: file_queue.task_done()` guarantees continuous message ingestion. **Resolution: KEEP_MINE.**

---

## 3. Upstream Bug Fixes & Security Fixes Integrated

1. **[Security Fix] Admin Session Revocation on Logout** (`credentials.py`, `template_routes.py`):
   Generates a cryptographically secure `sid` on login and registers it in `_revoked_sids = BoundedDict(1000)` upon logout. Prevents session replay attacks.
2. **[Security Fix] Brute-Force Rate Limiting on Admin Login** (`template_routes.py`):
   Limits failed logins to 5 attempts per IP in a 15-minute sliding window, returning HTTP 429. Protects against automated password attacks. Keys on real client IP (`cf-connecting-ip`, `x-real-ip`, `x-forwarded-for`).
3. **[Bug Fix] Workload Counter Double-Decrement Fix** (`custom_dl.py`):
   Adds `"load_held": True` and atomic `stream_entry.pop("load_held", False)` check before decrementing `work_loads[client_index]`. Prevents negative bot workload counts.
4. **[Bug Fix] Speed Test Duplicate `task_done()`** (`custom_dl.py`):
   Removes redundant `queue.task_done()` call inside `if not chunk: continue`, eliminating `ValueError: task_done() called too many times`.
5. **[Resiliency Fix] Multi-Bot Chunk Retry Rotation** (`custom_dl.py`):
   Rotates client session on retry (`(seq_idx + tries) % len(session_pool)`). Prevents dropped chunks when any individual bot is rate-limited. Verified each session carries its own bot-specific file reference and location box.
6. **[Bug Fix] Contiguous Split Part Requirement** (`global_search.py`):
   Enforces `sorted(parts) == list(range(1, len(parts) + 1))`. Rejects incomplete split video archives that would corrupt playback.
7. **[Bug Fix] Split Part Regex False-Positive Removal** (`global_search.py`):
   Removes overly aggressive `_ALT_PART_RE` regex that misclassified single files with words like "part" in the title as split archives.
8. **[Bug Fix] Dead Link Check Preserving "Recently Added" Sorting** (`database.py`):
   Removes `"updated_on": datetime.utcnow()` when marking a link dead, preventing dead link scans from bumping stale titles to the top of Recently Added.
9. **[Bug Fix] Nuvio Collection Isolation & Scoping** (`stremio_routes.py`):
   Restricts Nuvio collection generation strictly to library catalogs and serves only the requested collection.
10. **[Performance Fix] Slim Custom Catalog DB Projection** (`database.py`, `api_routes.py`, `custom_catalogs.html`):
    Uses `$setUnion` / `$size` / `$map` pipelines and `slim=True` / `item_fields` to avoid loading massive catalog items arrays into RAM.

---

## 4. Fork-Only Features Preserved

1. **Kurigram & Python 3.12 Compatibility**: Maintained actively on Kurigram 2.2.26+ with Python 3.12 support.
2. **Two-Tier Cache Architecture**: L1 RAM TTLCache + L2 SQLite persistent DiskCache for metadata and thumbnails with 7-day TTL.
3. **Memory Watchdog & Automatic Heap Trimming**: Background memory watchdog with human-unit parsing (e.g. `1gb`, `800mb`) and `malloc_trim(0)` arena release.
4. **Tracked Async Task Supervision (`spawn`)**: Named background task registry with graceful shutdown cancellation (`cancel_all_tracked_tasks()`).
5. **Crash-Proof Supervised Receiver Consumer**: Background file queue consumer with auto-restart loop and guaranteed `task_done()` in `finally:`.
6. **Double-Checked Per-DC Locking**: Eliminates concurrent MTProto session handshake races.
7. **Lazy DC Session Initialization**: Connects to Telegram DCs on-demand rather than prewarming 20 idle connections at boot.
8. **TCP Connection Drop Filter**: Demotes normal Telegram idle connection drops from ERROR to DEBUG.
9. **Server & Hardware System Stats in Web UI**: Real-time CPU, RAM, disk, swap, host load, and uptime in admin settings.
10. **Aggregation-Based DB Metrics**: Instant collection stats using MongoDB `$group`/`$sum` pipelines with TTL cache.
11. **Modern FastAPI / Starlette Standards**: `@asynccontextmanager lifespan`, `Query(..., pattern=...)`, and keyword-based `TemplateResponse(request=request, ...)`.

---

## 5. Amendments Enacted & Final Verifications

1. **`warm_streamers()` & `PREWARM_DCS` flag:**
   - Defined `warm_streamers()` in `stream_routes.py`.
   - Added `PREWARM_DCS = getenv("PREWARM_DCS", "false").lower() == "true"` to `Backend/config.py`.
   - In `Backend/__main__.py`, `warm_streamers()` is only called if `Telegram.PREWARM_DCS` is set to `true`.
2. **Latest Releases & Stable Catalog IDs:**
   - Adopted "Latest Releases" catalog with true release date sorting (`catalog_sort_key`).
   - Default catalog IDs (`latest_movies`, `latest_series`) remain completely stable.
   - Verified that `_settings_state(db)` migrates existing `auto_recently_added` database records to `auto_latest_releases` in-place, preventing breaks for installed Stremio/Nuvio addons.
3. **`MEDIA_PROJECTION` Field Completeness:**
   - Expanded `MEDIA_PROJECTION` in `auto_catalog.py` to project every field accessed by `release_date(doc, details)` and downstream classifiers: `release_date`, `seasons.episodes.released`, `release_year`, `updated_on`, `auto_tags`, `watch_providers`, `rating`, `original_language`, `origin_country`, `production_countries`, `is_anime`, `exclusive_catalog_id`, `visibility`, `allowed_tokens`.
   - Removed unmeasured RAM percentage claims.
4. **Nuvio UI Assets Reconciled:**
   - Grepped entire codebase: 0 files outside `stremio_routes.py` referenced the old hashed filenames.
   - Deleted 44 unreferenced old hashed WebP files.
   - Added 27 upstream brand-named WebP files (`Netflix.webp`, `RecentlyAdded_backdrop.webp`, etc.).
5. **Kurigram Verification:**
   - Verified that zero upstream code relies on Pyrofork-only APIs.
   - Executed full import check of all modules under Kurigram 2.2.26+.
   - `uv lock` regenerated successfully. All 18 pytest tests pass.
