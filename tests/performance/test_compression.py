import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.middleware import compression_router
from backend.performance.compression import (
    CompressionAlgorithm,
    CompressionEngine,
    CompressionProfile,
    CompressionResult,
    UnsupportedAlgorithmError,
    get_compression_engine,
)

BROTLI_AVAILABLE = CompressionEngine().supports(CompressionAlgorithm.BROTLI)


@pytest.fixture
def engine() -> CompressionEngine:
    return CompressionEngine(profile=CompressionProfile(threshold_bytes=0))


@pytest.fixture
def client(engine: CompressionEngine) -> TestClient:
    app = FastAPI()
    app.include_router(compression_router)
    app.dependency_overrides[get_compression_engine] = lambda: engine
    return TestClient(app)


def test_supports_reports_always_available_algorithms(engine: CompressionEngine):
    assert engine.supports(CompressionAlgorithm.GZIP) is True
    assert engine.supports(CompressionAlgorithm.DEFLATE) is True
    assert engine.supports(CompressionAlgorithm.IDENTITY) is True


def test_gzip_compress_and_decompress_round_trip(engine: CompressionEngine):
    payload = "hello world " * 50

    result = engine.compress(payload, algorithm=CompressionAlgorithm.GZIP)

    assert isinstance(result, CompressionResult)
    assert result.algorithm == CompressionAlgorithm.GZIP
    assert result.compressed_size < result.original_size

    restored = engine.decompress(result.data, algorithm=CompressionAlgorithm.GZIP)
    assert restored.decode("utf-8") == payload


def test_deflate_compress_and_decompress_round_trip(engine: CompressionEngine):
    payload = "hello world " * 50

    result = engine.compress(payload, algorithm=CompressionAlgorithm.DEFLATE)
    restored = engine.decompress(result.data, algorithm=CompressionAlgorithm.DEFLATE)

    assert restored.decode("utf-8") == payload


def test_identity_does_not_change_payload(engine: CompressionEngine):
    payload = b"raw bytes"

    result = engine.compress(payload, algorithm=CompressionAlgorithm.IDENTITY)

    assert result.data == payload
    assert result.compressed_size == result.original_size


def test_threshold_forces_identity_for_small_payloads(engine: CompressionEngine):
    result = engine.compress("tiny", algorithm=CompressionAlgorithm.GZIP, threshold_bytes=1000)

    assert result.algorithm == CompressionAlgorithm.IDENTITY


@pytest.mark.skipif(not BROTLI_AVAILABLE, reason="brotli library not installed")
def test_brotli_compress_and_decompress_round_trip(engine: CompressionEngine):
    payload = "hello world " * 50

    result = engine.compress(payload, algorithm=CompressionAlgorithm.BROTLI)
    restored = engine.decompress(result.data, algorithm=CompressionAlgorithm.BROTLI)

    assert restored.decode("utf-8") == payload


@pytest.mark.skipif(BROTLI_AVAILABLE, reason="only relevant when brotli is unavailable")
def test_brotli_unavailable_raises(engine: CompressionEngine):
    with pytest.raises(UnsupportedAlgorithmError):
        engine.compress("data", algorithm=CompressionAlgorithm.BROTLI)


def test_negotiate_picks_highest_quality_supported(engine: CompressionEngine):
    algorithm = engine.negotiate("gzip;q=0.5, deflate;q=0.9")

    assert algorithm == CompressionAlgorithm.DEFLATE


def test_negotiate_excludes_zero_quality(engine: CompressionEngine):
    algorithm = engine.negotiate("gzip;q=0, deflate;q=0.5")

    assert algorithm == CompressionAlgorithm.DEFLATE


def test_negotiate_falls_back_to_identity_when_nothing_acceptable(engine: CompressionEngine):
    algorithm = engine.negotiate("gzip;q=0, deflate;q=0, br;q=0, identity;q=0")

    assert algorithm == CompressionAlgorithm.IDENTITY


def test_negotiate_empty_header_prefers_configured_order(engine: CompressionEngine):
    algorithm = engine.negotiate("")

    assert algorithm == CompressionAlgorithm.IDENTITY


def test_negotiate_wildcard_matches_supported_algorithm(engine: CompressionEngine):
    algorithm = engine.negotiate("*;q=1.0")

    assert algorithm in (CompressionAlgorithm.BROTLI, CompressionAlgorithm.GZIP)
    assert engine.supports(algorithm)


def test_negotiate_skips_unsupported_brotli_when_unavailable():
    engine = CompressionEngine()
    if engine.supports(CompressionAlgorithm.BROTLI):
        pytest.skip("brotli is installed in this environment")

    algorithm = engine.negotiate("br;q=1.0, gzip;q=0.5")

    assert algorithm == CompressionAlgorithm.GZIP


def test_compress_stream_yields_data_decompressible_as_whole(engine: CompressionEngine):
    chunks = ["hello ", "streaming ", "world " * 20]

    compressed = b"".join(engine.compress_stream(chunks, algorithm=CompressionAlgorithm.GZIP))
    restored = engine.decompress(compressed, algorithm=CompressionAlgorithm.GZIP)

    assert restored.decode("utf-8") == "".join(chunks)


def test_compress_stream_identity_passes_through(engine: CompressionEngine):
    chunks = [b"a", b"b", b"c"]

    result = b"".join(engine.compress_stream(chunks, algorithm=CompressionAlgorithm.IDENTITY))

    assert result == b"abc"


def test_stats_tracks_usage_per_algorithm(engine: CompressionEngine):
    engine.compress("payload one " * 10, algorithm=CompressionAlgorithm.GZIP)
    engine.compress("payload two " * 10, algorithm=CompressionAlgorithm.GZIP)

    stats = engine.stats()

    gzip_stats = stats["by_algorithm"][CompressionAlgorithm.GZIP.value]
    assert gzip_stats["count"] == 2
    assert gzip_stats["original_bytes"] > 0
    assert gzip_stats["average_ratio"] is not None


def test_decompress_unknown_algorithm_raises(engine: CompressionEngine):
    with pytest.raises(UnsupportedAlgorithmError):
        engine.decompress(b"data", algorithm="unknown-algorithm")


def test_api_get_compression_config(client: TestClient):
    response = client.get("/performance/compression")

    assert response.status_code == 200
    body = response.json()
    assert "preferred_algorithms" in body
    assert "gzip" in body["supported_algorithms"]


def test_api_test_compression_with_explicit_algorithm(client: TestClient):
    response = client.post(
        "/performance/compression/test",
        json={"data": "hello world " * 20, "algorithm": "gzip"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["algorithm"] == "gzip"
    assert body["compressed_size"] < body["original_size"]


def test_api_test_compression_negotiates_from_accept_encoding(client: TestClient):
    response = client.post(
        "/performance/compression/test",
        json={"data": "hello world " * 20, "accept_encoding": "deflate;q=1.0, gzip;q=0.1"},
    )

    assert response.status_code == 200
    assert response.json()["algorithm"] == "deflate"


def test_api_test_compression_rejects_unknown_algorithm(client: TestClient):
    response = client.post(
        "/performance/compression/test", json={"data": "hello", "algorithm": "lz4"}
    )

    assert response.status_code == 422


def test_api_compression_stats(client: TestClient):
    client.post("/performance/compression/test", json={"data": "hello world " * 20, "algorithm": "gzip"})

    response = client.get("/performance/compression/stats")

    assert response.status_code == 200
    assert response.json()["by_algorithm"]["gzip"]["count"] == 1
