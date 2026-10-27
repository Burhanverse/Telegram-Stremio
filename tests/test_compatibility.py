import pytest
from datetime import datetime, timezone
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
    """Verify version 5.3.0 and Nuvio collection routes."""
    import Backend
    assert Backend.__version__ == "5.3.0"

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
