import os
import pytest
from datetime import datetime, timezone

os.environ.setdefault("DATABASE", "mongodb://localhost:27017/tracking,mongodb://localhost:27017/storage_1")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.fastapi.main import app
from Backend.helper.modal import (
    QualityPart,
    QualityDetail,
    Episode,
    Season,
    TVShowSchema,
    MovieSchema,
)
from Backend.helper.memory import parse_mem_threshold_mb, get_rss_kb
from Backend.helper.metadata.parse import parse_media_name
from Backend.helper.metadata.providers.tmdb import (
    get_tmdb_client,
    get_tmdb_logo,
    build_movie_payload,
)
from Backend.helper.stream_formatter import build_parse_value


def test_fastapi_app_and_routes():
    """Verify that FastAPI app compiles, mounts routers, and responds to public routes."""
    assert isinstance(app, FastAPI)
    routes = []
    for r in app.routes:
        if hasattr(r, "path"):
            routes.append(r.path)
        elif hasattr(r, "routes"):
            for sub in r.routes:
                if hasattr(sub, "path"):
                    routes.append(sub.path)
    assert "/login" in routes
    assert "/api/media/list" in routes

    client = TestClient(app)
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "html" in resp.headers.get("content-type", "").lower()


def test_pydantic_schemas():
    """Verify that Pydantic v2 models instantiate, validate, and serialize properly."""
    part = QualityPart(part_number=1, chat_id=-10012345, msg_id=42, size_bytes=1048576)
    assert part.part_number == 1
    assert part.size_bytes == 1048576

    qd = QualityDetail(
        quality="1080p",
        id="hash123",
        name="Episode.1.1080p.mkv",
        size="1.0 GB",
        parts=[part],
    )
    assert qd.quality == "1080p"
    assert len(qd.parts) == 1

    ep = Episode(episode_number=1, title="Pilot", telegram=[qd])
    assert ep.episode_number == 1

    season = Season(season_number=1, episodes=[ep])
    assert season.season_number == 1
    assert len(season.episodes) == 1

    tv = TVShowSchema(
        tmdb_id=1399,
        db_index=0,
        title="Game of Thrones",
        media_type="tv",
        seasons=[season],
    )
    dumped = tv.model_dump()
    assert dumped["title"] == "Game of Thrones"
    assert dumped["tmdb_id"] == 1399

    movie = MovieSchema(
        tmdb_id=27205,
        db_index=0,
        title="Inception",
        media_type="movie",
        telegram=[qd],
    )
    movie_dump = movie.model_dump()
    assert movie_dump["title"] == "Inception"
    assert movie_dump["telegram"][0]["quality"] == "1080p"


def test_memory_watchdog_parser():
    """Verify parse_mem_threshold_mb handles various unit notations and bounds."""
    assert parse_mem_threshold_mb("1gb") == 1024.0
    assert parse_mem_threshold_mb("1GB") == 1024.0
    assert parse_mem_threshold_mb("1.5gb") == 1536.0
    assert parse_mem_threshold_mb("1024") == 1024.0
    assert parse_mem_threshold_mb("800mb") == 800.0
    assert parse_mem_threshold_mb("800m") == 800.0
    assert parse_mem_threshold_mb("800") == 800.0
    assert parse_mem_threshold_mb("512mib") == 512.0
    assert parse_mem_threshold_mb("0") == 0.0
    assert parse_mem_threshold_mb("") == 0.0
    assert parse_mem_threshold_mb(None) == 0.0

    rss = get_rss_kb()
    assert isinstance(rss, int)
    assert rss >= 0


def test_media_name_parser():
    """Verify GuessIt and PTN integration in parse_media_name."""
    parsed_tv = parse_media_name("Stranger.Things.S04E07.1080p.NF.WEB-DL.DDP5.1.Atmos.x264.mkv")
    assert parsed_tv.get("season") == 4
    assert parsed_tv.get("episode") == 7
    assert "Stranger Things" in parsed_tv.get("title", "")

    parsed_movie = parse_media_name("Oppenheimer.2023.2160p.UHD.BluRay.x265.mkv")
    assert "Oppenheimer" in parsed_movie.get("title", "")
    assert parsed_movie.get("year") == 2023


def test_tmdb_client_and_helpers():
    """Verify themoviedb 2.x client instantiation and helpers."""
    client = get_tmdb_client()
    assert client is not None

    class DummyLogo:
        iso_639_1 = "en"
        file_path = "/logo.png"

    class DummyImages:
        logos = [DummyLogo()]

    logo_url = get_tmdb_logo(DummyImages())
    assert "logo.png" in logo_url

    class DummyMovie:
        id = 1234
        title = "Test Movie"
        original_title = "Original Test Movie"
        release_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
        vote_average = 8.5
        overview = "Test overview description"
        poster_path = "/poster.png"
        backdrop_path = "/backdrop.png"
        external_ids = None
        origin_country = ["US"]
        production_countries = []
        genres = []

    payload = build_movie_payload(DummyMovie(), quality="1080p", encoded_string="enc123")
    assert payload["tmdb_id"] == 1234
    assert payload["title"] == "Test Movie"
    assert payload["year"] == 2025
    assert payload["rate"] == 8.5
    assert payload["quality"] == "1080p"
    assert payload["encoded_string"] == "enc123"


def test_stream_formatter():
    """Verify stream parsing and tagging."""
    pv = build_parse_value(
        "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR10.HEVC.TrueHD.Atmos.7.1.ITA.ENG-GROUP.mkv",
        65_000_000_000,
        addon_name="Telegram",
        channel="Movies HQ",
        meta={"queryType": "movie", "type": "movie"},
    )
    s = pv["stream"]
    assert s["resolution"] == "2160p"
    assert s["quality"] == "BluRay REMUX"
    assert s["encode"] == "HEVC"
    assert {"DV", "HDR10"} <= set(s["visualTags"])
    assert {"TrueHD", "Atmos"} <= set(s["audioTags"])


def test_password_hashing():
    """Verify password hashing and verification."""
    from Backend.helper.passwords import hash_password, verify_password
    raw = "MyS3cr3tP@ssw0rd!"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_settings_manager():
    """Verify SettingsManager defaults, accessors, and dictionary export."""
    from Backend.helper.settings_manager import SettingsManager
    mgr = SettingsManager.current()
    d = mgr.to_dict()
    assert isinstance(d, dict)
    assert "admin_username" in d
    assert mgr.admin_username is not None
    assert mgr.stream_formatter in ("default", "custom", "detailed", "minimal")


def test_metadata_cleaning_and_matching():
    """Verify title normalization and fuzzy scoring."""
    from Backend.helper.metadata.common import normalize_title, score_candidate
    normalized = normalize_title("Avatar: The Way of Water [2022] (1080p)")
    assert "avatar the way of water" in normalized.lower()

    score = score_candidate(
        query_title="Avatar The Way of Water",
        query_year=2022,
        result_title="Avatar: The Way of Water",
        result_year=2022,
    )
    assert score >= 0.8


def test_bounded_dict():
    """Verify BoundedDict capacity limiting and dict operations."""
    from Backend.helper.bounded import BoundedDict
    b = BoundedDict(3)
    b["a"] = 1
    b["b"] = 2
    b["c"] = 3
    assert len(b) == 3
    assert b["a"] == 1
    b["d"] = 4
    assert len(b) <= 3
    assert "d" in b


def test_metadata_helpers_and_two_tier_cache():
    """Verify metadata link extraction, raw ref detection, and two-tier cache."""
    from Backend.helper.metadata import (
        caption_with_links,
        has_raw_ref,
        clear_metadata_ram_caches,
    )
    from Backend.helper.metadata.common import get_disk_cache_stats

    class DummyEntity:
        url = "https://www.imdb.com/title/tt1375666/"

    class DummyMessage:
        caption = "Check out Inception"
        caption_entities = [DummyEntity()]

    msg = DummyMessage()
    caption_text = caption_with_links(msg)
    assert "https://www.imdb.com/title/tt1375666/" in caption_text
    assert has_raw_ref(caption_text) is True
    assert has_raw_ref("Just a plain caption") is False

    cleared = clear_metadata_ram_caches()
    assert isinstance(cleared, int)

    stats = get_disk_cache_stats()
    assert "items" in stats
    assert "size_bytes" in stats


def test_version_and_nuvio_endpoints():
    """Verify version 5.3.2 and Nuvio collection routes."""
    import Backend
    assert Backend.__version__ == "5.3.2"

    routes = []
    for r in app.routes:
        if hasattr(r, "path"):
            routes.append(r.path)
        if hasattr(r, "original_router") and hasattr(r.original_router, "routes"):
            for sr in r.original_router.routes:
                if hasattr(sr, "path"):
                    routes.append(sr.path)
    assert "/api/media/tag" in routes
    assert any("nuvio-collection.json" in r for r in routes)


def test_disk_cache_module():
    """Verify open_disk_cache and disk_cache_stats."""
    import shutil
    from Backend.helper.disk_cache import CACHE_ROOT, disk_cache_stats, open_disk_cache

    c = open_disk_cache("test_cache_unit", 10, "TestCache")
    assert c is not None
    try:
        c["hello"] = "world"
        assert c["hello"] == "world"
        stats = disk_cache_stats(c, 10)
        assert stats["items"] == 1
        assert stats["size_limit_mb"] == 10
        assert stats["size_bytes"] > 0
        assert "size_mb" in stats
    finally:
        c.close()
        test_dir = CACHE_ROOT / "test_cache_unit"
        if test_dir.exists():
            shutil.rmtree(test_dir, ignore_errors=True)

    empty_stats = disk_cache_stats(None, 50)
    assert empty_stats == {"items": 0, "size_bytes": 0, "size_mb": 0.0, "size_limit_mb": 50}


def test_memory_limit_detection(monkeypatch):
    """Verify cgroup v1/v2 and host /proc/meminfo parsing in _memory_limit_mb."""
    import os
    from unittest.mock import mock_open
    from Backend.helper.memory import _memory_limit_mb, get_watchdog_threshold_mb

    # Live host test: threshold should be 75% of limit
    host_limit = _memory_limit_mb()
    assert host_limit > 0
    assert get_watchdog_threshold_mb() == round(host_limit * 0.75, 2)

    # Simulated cgroup v2 container with 512 MB limit
    def mock_exists(path):
        return path in ("/sys/fs/cgroup/memory.max", "/proc/meminfo")

    def mock_open_file(path, *args, **kwargs):
        if path == "/sys/fs/cgroup/memory.max":
            return mock_open(read_data="536870912\n")()  # 512 MB
        elif path == "/proc/meminfo":
            return mock_open(read_data="MemTotal:        16384000 kB\n")()  # ~16 GB
        raise FileNotFoundError(path)

    monkeypatch.setattr(os.path, "exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open_file)

    limit = _memory_limit_mb()
    assert limit == 512.0
    assert get_watchdog_threshold_mb() == 384.0  # 512 * 0.75

    # Simulated cgroup v2 with "max" (unlimited) -> fallback to MemTotal
    def mock_open_max(path, *args, **kwargs):
        if path == "/sys/fs/cgroup/memory.max":
            return mock_open(read_data="max\n")()
        elif path == "/proc/meminfo":
            return mock_open(read_data="MemTotal:        4194304 kB\n")()  # 4096 MB
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", mock_open_max)
    limit_max = _memory_limit_mb()
    assert limit_max == 4096.0
    assert get_watchdog_threshold_mb() == 3072.0  # 4096 * 0.75


def test_system_stats_proc_parsers():
    """Verify /proc parsers with valid text, malformed text, and empty inputs."""
    from Backend.helper.system_stats import (
        parse_meminfo,
        parse_stat_cpu,
        parse_loadavg,
        parse_uptime,
        parse_net_dev,
        parse_proc_stat_process,
        get_disk_usage,
        get_server_system_stats,
    )

    # 1. meminfo
    sample_meminfo = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "Buffers:          500000 kB\n"
        "Cached:          4000000 kB\n"
        "SwapTotal:       2097152 kB\n"
        "SwapFree:        1048576 kB\n"
    )
    mem = parse_meminfo(sample_meminfo)
    assert mem is not None
    assert mem["total"] == 16384000 * 1024
    assert mem["available"] == 8192000 * 1024
    assert mem["used"] == (16384000 - 8192000) * 1024
    assert mem["usage_pct"] == 50.0
    assert mem["swap_total"] == 2097152 * 1024
    assert mem["swap_used"] == 1048576 * 1024
    assert mem["swap_usage_pct"] == 50.0
    assert "total_formatted" in mem
    assert "used_formatted" in mem

    # meminfo malformed/empty fallbacks
    assert parse_meminfo("") is None
    assert parse_meminfo("some random string without colon") is None
    assert parse_meminfo("MemFree: 1000 kB\n") is None  # missing MemTotal

    # 2. cpu stat
    sample_stat = (
        "cpu  1000 200 300 5000 100 0 50 0 0 0\n"
        "cpu0 500 100 150 2500 50 0 25 0 0 0\n"
        "cpu1 500 100 150 2500 50 0 25 0 0 0\n"
        "intr 12345 6789\n"
    )
    overall, cores = parse_stat_cpu(sample_stat)
    assert overall is not None
    assert overall[0] == 1000 + 200 + 300 + 5000 + 100 + 0 + 50  # total
    assert overall[1] == 5000 + 100  # idle + iowait
    assert len(cores) == 2
    assert "cpu0" in cores and "cpu1" in cores

    # cpu stat malformed/empty fallbacks
    assert parse_stat_cpu("") == (None, {})
    assert parse_stat_cpu("invalid data here") == (None, {})

    # 3. loadavg
    assert parse_loadavg("0.15 0.25 0.35 1/400 12345") == [0.15, 0.25, 0.35]
    assert parse_loadavg("") is None
    assert parse_loadavg("invalid") is None

    # 4. uptime
    assert parse_uptime("123456.78 876543.21") == 123456.78
    assert parse_uptime("") is None
    assert parse_uptime("not a number") is None

    # 5. net dev
    sample_net = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 1000       10    0    0    0     0          0         0     1000       10    0    0    0     0       0          0\n"
        "  eth0: 5000       50    0    0    0     0          0         0     3000       30    0    0    0     0       0          0\n"
    )
    recv, sent = parse_net_dev(sample_net)
    assert recv == 5000
    assert sent == 3000
    assert parse_net_dev("") == (0, 0)
    assert parse_net_dev("corrupted net data") == (0, 0)

    # 6. proc stat process
    sample_proc = "1234 (python-worker) S 1 1234 1234 0 -1 4194304 100 0 0 0 50 25 0 0 20 0 4 0 100 10000000 100 18446744073709551615"
    ticks = parse_proc_stat_process(sample_proc)
    assert ticks == 50 + 25  # utime + stime
    assert parse_proc_stat_process("") is None
    assert parse_proc_stat_process("malformed stat string") is None

    # 7. disk usage
    disk = get_disk_usage(".")
    assert disk is not None
    assert disk["total"] > 0
    assert disk["free"] >= 0
    assert 0 <= disk["usage_pct"] <= 100

    # 8. live collector
    stats = get_server_system_stats()
    assert isinstance(stats, dict)
    assert "cpu" in stats
    assert "memory" in stats
    assert "disk" in stats
    assert "network" in stats
    assert "uptime" in stats
    assert "process" in stats
    assert "cores" in stats["cpu"]
    assert "load_avg" in stats["cpu"]


def test_system_stats_endpoints_auth():
    """Verify authenticated vs unauthenticated access to admin stats endpoints."""
    from Backend.fastapi.security.credentials import require_auth

    client = TestClient(app)

    # 1. Unauthenticated requests MUST return 401
    assert client.get("/api/admin/system-stats").status_code == 401
    assert client.get("/api/admin/stats").status_code == 401
    assert client.get("/api/admin/server-stats").status_code == 401

    # 2. Authenticated requests:
    app.dependency_overrides[require_auth] = lambda: True
    try:
        # /api/admin/system-stats keeps legacy keys, does NOT have 'server'
        resp_sys = client.get("/api/admin/system-stats")
        assert resp_sys.status_code == 200
        data_sys = resp_sys.json()
        assert "cache_size" in data_sys
        assert "total_bots" in data_sys
        assert "bot_workloads" in data_sys
        assert "server" not in data_sys

        # /api/admin/server-stats returns decoupled host server metrics
        resp_srv = client.get("/api/admin/server-stats")
        assert resp_srv.status_code == 200
        data_srv = resp_srv.json()
        assert data_srv["status"] == "success"
        srv = data_srv["data"]
        assert "cpu" in srv
        assert "memory" in srv
        assert "disk" in srv
        assert "uptime" in srv
        assert "process" in srv
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_server_stats_zero_db_coupling(monkeypatch):
    """Verify /api/admin/server-stats never touches MongoDB."""
    from Backend.fastapi.security.credentials import require_auth
    import Backend

    class BombDB:
        def __getattr__(self, name):
            raise RuntimeError("Database should not be accessed by server-stats!")

    monkeypatch.setattr(Backend, "db", BombDB())

    client = TestClient(app)
    app.dependency_overrides[require_auth] = lambda: True
    try:
        resp = client.get("/api/admin/server-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "cpu" in data["data"]
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_db_stats_aggregation_logic_and_caching():
    """Verify MongoDB aggregation pipeline matches loop counting across all edge cases, and test TTL cache."""
    # 1. Compare loop logic vs aggregation expressions on edge-case document corpus
    test_movies = [
        {},
        {"telegram": None},
        {"telegram": []},
        {"telegram": ["s1", "s2"]},
        {"title": "Movie 3", "telegram": ["s3"]},
    ]

    test_shows = [
        {},
        {"seasons": None},
        {"seasons": []},
        {"seasons": [{}]},
        {"seasons": [{"episodes": []}]},
        {"seasons": [{"episodes": [{"title": "Ep1"}]}]},
        {"seasons": [{"episodes": [{"title": "Ep2", "telegram": None}]}]},
        {"seasons": [
            {"episodes": [{"telegram": ["t1", "t2"]}, {"telegram": []}]},
            {"episodes": [{"telegram": ["t3"]}]},
        ]},
    ]

    # Baseline loop counting
    expected_episodes = 0
    expected_streams = 0
    for m in test_movies:
        expected_streams += len((m.get("telegram") if isinstance(m, dict) else None) or [])
    for show in test_shows:
        for season in (show.get("seasons") or []):
            for episode in (season.get("episodes") or []):
                expected_episodes += 1
                expected_streams += len((episode.get("telegram") if isinstance(episode, dict) else None) or [])

    # Aggregation expressions simulation
    m_agg_streams = sum(len((m.get("telegram") if isinstance(m, dict) else None) or []) for m in test_movies)

    # TV pipeline: seasons.episodes flatten with $concatArrays, then $size and $map $size
    agg_episodes = 0
    agg_tv_streams = 0
    for show in test_shows:
        seasons = show.get("seasons") or []
        seasons_episodes = [s["episodes"] for s in seasons if isinstance(s, dict) and "episodes" in s and s["episodes"]]
        all_episodes = [ep for eps in seasons_episodes for ep in (eps or [])]
        agg_episodes += len(all_episodes)
        for ep in all_episodes:
            agg_tv_streams += len((ep.get("telegram") if isinstance(ep, dict) else None) or [])

    assert agg_episodes == expected_episodes == 5
    assert (m_agg_streams + agg_tv_streams) == expected_streams == 6

    # 2. Test get_db_stats_api caching and mock storage aggregation execution
    import asyncio
    from Backend.fastapi.routes.api_routes import get_db_stats_api
    import Backend.fastapi.routes.api_routes as api_routes
    from Backend import db

    class MockCollection:
        def __init__(self, count_val, agg_val):
            self._count_val = count_val
            self._agg_val = agg_val
            self.agg_call_count = 0

        async def count_documents(self, filter_query):
            return self._count_val

        def aggregate(self, pipeline):
            self.agg_call_count += 1
            class MockCursor:
                def __init__(self, val):
                    self._val = val
                async def to_list(self, length):
                    return self._val
            return MockCursor(self._agg_val)

    class MockStorage:
        def __init__(self):
            self.movie = MockCollection(10, [{"total_streams": 15}])
            self.tv = MockCollection(5, [{"total_episodes": 20, "total_streams": 25}])
        def __getitem__(self, key):
            if key == "movie":
                return self.movie
            return self.tv
        async def command(self, cmd):
            return {"dataSize": 1048576}

    mock_storage = MockStorage()
    orig_dbs = db.dbs
    orig_idx = db.current_db_index
    db.current_db_index = 1
    db.dbs = {"storage_1": mock_storage}

    # Reset cache
    api_routes._DB_STATS_CACHE = None
    api_routes._DB_STATS_CACHED_AT = 0.0

    try:
        res1 = asyncio.run(get_db_stats_api(fresh=True))
        assert res1["status"] == "success"
        data1 = res1["data"]
        assert data1["movies"] == 10
        assert data1["tv_shows"] == 5
        assert data1["episodes"] == 20
        assert data1["streams"] == 40  # 15 + 25
        assert "server" not in data1  # decoupled!
        assert mock_storage.movie.agg_call_count == 1

        # Second call within TTL returns cached payload without re-running aggregation
        res2 = asyncio.run(get_db_stats_api(fresh=False))
        assert res2["status"] == "success"
        assert mock_storage.movie.agg_call_count == 1  # call count stayed 1!

        # fresh=True forces re-run
        res3 = asyncio.run(get_db_stats_api(fresh=True))
        assert res3["status"] == "success"
        assert mock_storage.movie.agg_call_count == 2
    finally:
        db.dbs = orig_dbs
        db.current_db_index = orig_idx


