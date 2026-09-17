from __future__ import annotations

import base64
import gzip
import zlib
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Iterable, Iterator, Optional, Union

try:
    import brotli

    _BROTLI_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dependency
    brotli = None
    _BROTLI_AVAILABLE = False


class CompressionAlgorithm(str, Enum):
    """A content-encoding supported by the compression engine."""

    GZIP = "gzip"
    BROTLI = "br"
    DEFLATE = "deflate"
    IDENTITY = "identity"


class UnsupportedAlgorithmError(ValueError):
    pass


_ALGORITHM_TOKENS = {
    CompressionAlgorithm.GZIP: "gzip",
    CompressionAlgorithm.BROTLI: "br",
    CompressionAlgorithm.DEFLATE: "deflate",
    CompressionAlgorithm.IDENTITY: "identity",
}


def _parse_accept_encoding(header: str) -> dict:
    weights: dict = {}
    for part in (header or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, _, params = part.partition(";")
        name = name.strip().lower()
        quality = 1.0
        params = params.strip()
        if params.startswith("q="):
            try:
                quality = float(params[2:])
            except ValueError:
                quality = 1.0
        weights[name] = quality
    return weights


def _coerce_bytes(data: Union[bytes, str]) -> bytes:
    return data.encode("utf-8") if isinstance(data, str) else data


@dataclass(frozen=True)
class CompressionProfile:
    """The configuration an engine uses to pick and apply compression."""

    name: str = "default"
    threshold_bytes: int = 0
    level: int = 6
    preferred_algorithms: tuple = (
        CompressionAlgorithm.BROTLI,
        CompressionAlgorithm.GZIP,
        CompressionAlgorithm.DEFLATE,
        CompressionAlgorithm.IDENTITY,
    )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "threshold_bytes": self.threshold_bytes,
            "level": self.level,
            "preferred_algorithms": [alg.value for alg in self.preferred_algorithms],
        }


@dataclass(frozen=True)
class CompressionResult:
    """The outcome of compressing a single payload."""

    algorithm: CompressionAlgorithm
    original_size: int
    compressed_size: int
    ratio: float
    data: bytes = field(repr=False)

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm.value,
            "original_size": self.original_size,
            "compressed_size": self.compressed_size,
            "ratio": self.ratio,
            "data_base64": base64.b64encode(self.data).decode("ascii"),
        }


class CompressionEngine:
    """Compresses and decompresses payloads, with Accept-Encoding negotiation."""

    def __init__(self, *, profile: Optional[CompressionProfile] = None) -> None:
        self.profile = profile or CompressionProfile()
        self._lock = Lock()
        self._stats: dict = {
            algorithm: {"count": 0, "original_bytes": 0, "compressed_bytes": 0}
            for algorithm in CompressionAlgorithm
        }

    def supports(self, algorithm: CompressionAlgorithm) -> bool:
        if algorithm == CompressionAlgorithm.BROTLI:
            return _BROTLI_AVAILABLE
        return algorithm in CompressionAlgorithm

    def negotiate(self, accept_encoding: str) -> CompressionAlgorithm:
        weights = _parse_accept_encoding(accept_encoding)
        wildcard_q = weights.get("*")
        candidates = []
        for algorithm in self.profile.preferred_algorithms:
            token = _ALGORITHM_TOKENS[algorithm]
            if token in weights:
                quality = weights[token]
            elif wildcard_q is not None:
                quality = wildcard_q
            else:
                continue
            if quality <= 0 or not self.supports(algorithm):
                continue
            candidates.append((quality, algorithm))
        if not candidates:
            return CompressionAlgorithm.IDENTITY
        candidates.sort(key=lambda item: (-item[0], self.profile.preferred_algorithms.index(item[1])))
        return candidates[0][1]

    def compress(
        self,
        data: Union[bytes, str],
        *,
        algorithm: CompressionAlgorithm = CompressionAlgorithm.GZIP,
        threshold_bytes: Optional[int] = None,
        level: Optional[int] = None,
    ) -> CompressionResult:
        raw = _coerce_bytes(data)
        threshold = self.profile.threshold_bytes if threshold_bytes is None else threshold_bytes
        effective_level = self.profile.level if level is None else level
        effective_algorithm = algorithm
        if len(raw) < threshold:
            effective_algorithm = CompressionAlgorithm.IDENTITY

        if effective_algorithm == CompressionAlgorithm.IDENTITY:
            compressed = raw
        elif effective_algorithm == CompressionAlgorithm.GZIP:
            compressed = gzip.compress(raw, compresslevel=effective_level)
        elif effective_algorithm == CompressionAlgorithm.DEFLATE:
            compressed = zlib.compress(raw, effective_level)
        elif effective_algorithm == CompressionAlgorithm.BROTLI:
            if not self.supports(CompressionAlgorithm.BROTLI):
                raise UnsupportedAlgorithmError("brotli is not available")
            compressed = brotli.compress(raw, quality=effective_level)
        else:
            raise UnsupportedAlgorithmError(str(effective_algorithm))

        result = CompressionResult(
            algorithm=effective_algorithm,
            original_size=len(raw),
            compressed_size=len(compressed),
            ratio=(len(compressed) / len(raw)) if raw else 1.0,
            data=compressed,
        )
        self._record(result)
        return result

    def decompress(self, data: bytes, *, algorithm: CompressionAlgorithm) -> bytes:
        if algorithm == CompressionAlgorithm.IDENTITY:
            return data
        if algorithm == CompressionAlgorithm.GZIP:
            return gzip.decompress(data)
        if algorithm == CompressionAlgorithm.DEFLATE:
            return zlib.decompress(data)
        if algorithm == CompressionAlgorithm.BROTLI:
            if not self.supports(CompressionAlgorithm.BROTLI):
                raise UnsupportedAlgorithmError("brotli is not available")
            return brotli.decompress(data)
        raise UnsupportedAlgorithmError(str(algorithm))

    def compress_stream(
        self,
        chunks: Iterable[Union[bytes, str]],
        *,
        algorithm: CompressionAlgorithm = CompressionAlgorithm.GZIP,
        level: Optional[int] = None,
    ) -> Iterator[bytes]:
        effective_level = self.profile.level if level is None else level

        if algorithm == CompressionAlgorithm.IDENTITY:
            for chunk in chunks:
                yield _coerce_bytes(chunk)
            return

        if algorithm == CompressionAlgorithm.GZIP:
            compressor = zlib.compressobj(effective_level, zlib.DEFLATED, zlib.MAX_WBITS | 16)
        elif algorithm == CompressionAlgorithm.DEFLATE:
            compressor = zlib.compressobj(effective_level)
        elif algorithm == CompressionAlgorithm.BROTLI:
            if not self.supports(CompressionAlgorithm.BROTLI):
                raise UnsupportedAlgorithmError("brotli is not available")
            brotli_compressor = brotli.Compressor(quality=effective_level)
            for chunk in chunks:
                out = brotli_compressor.process(_coerce_bytes(chunk))
                if out:
                    yield out
            tail = brotli_compressor.finish()
            if tail:
                yield tail
            return
        else:
            raise UnsupportedAlgorithmError(str(algorithm))

        for chunk in chunks:
            out = compressor.compress(_coerce_bytes(chunk))
            if out:
                yield out
        tail = compressor.flush()
        if tail:
            yield tail

    def _record(self, result: CompressionResult) -> None:
        with self._lock:
            bucket = self._stats[result.algorithm]
            bucket["count"] += 1
            bucket["original_bytes"] += result.original_size
            bucket["compressed_bytes"] += result.compressed_size

    def stats(self) -> dict:
        with self._lock:
            by_algorithm = {}
            for algorithm, bucket in self._stats.items():
                average_ratio = (
                    bucket["compressed_bytes"] / bucket["original_bytes"]
                    if bucket["original_bytes"]
                    else None
                )
                by_algorithm[algorithm.value] = {**bucket, "average_ratio": average_ratio}
            return {"by_algorithm": by_algorithm}


_compression_engine = CompressionEngine()


def get_compression_engine() -> CompressionEngine:
    return _compression_engine
