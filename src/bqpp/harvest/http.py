"""The only module in the project that opens a socket.

Harvest etiquette (spec §6) is a hard requirement, so it lives in exactly one
place and is unit-testable: descriptive User-Agent, at most one request per second
per host, an on-disk cache keyed by URL hash so an unchanged file is never
re-downloaded, and a provenance manifest row for every byte that crosses the wire.

`opener`, `sleep` and `clock` are injectable so the tests exercise all of that
without a network or a real delay.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

Opener = Callable[[str, dict], tuple[bytes, int, dict]]


def normalise_url(url: str) -> str:
    """Upgrade http:// to https://.

    Two thirds of the links on the OAB exam index pages are emitted as http://,
    and http://s.oab.org.br returns 502 Bad Gateway while the identical https://
    path returns 200. Without this one line the harvest silently loses most of
    the archive — every exam numbered XXXI or older.
    """
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


@dataclass
class FetchResult:
    url: str
    body: bytes
    sha256: str
    from_cache: bool
    local_path: Path
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


class FetchError(RuntimeError):
    """The URL could not be retrieved. The caller decides whether that is fatal."""


def _urllib_opener(url: str, headers: dict) -> tuple[bytes, int, dict]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read(), resp.status, dict(resp.headers)


class Fetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        cache_dir: Path,
        db: Any = None,
        min_interval: float = 1.5,
        offline: bool = False,
        opener: Opener | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.db = db
        self.min_interval = min_interval
        self.offline = offline
        self._opener = opener or _urllib_opener
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}

    # ---- cache --------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        """Keyed by URL hash, not basename: the OAB serves distinct files that
        share a name, and its own filenames are opaque UUIDs."""
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"

    @staticmethod
    def _meta_path(cache_path: Path) -> Path:
        return cache_path.with_suffix(".meta.json")

    # ---- etiquette ----------------------------------------------------
    def _wait_turn(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            elapsed = self._clock() - last
            if elapsed < self.min_interval:
                self._sleep(self.min_interval - elapsed)
        self._last_request[host] = self._clock()

    # ---- the one public entry point -----------------------------------
    def get(self, url: str) -> FetchResult:
        url = normalise_url(url)
        cache_path = self._cache_path(url)

        if cache_path.exists():
            body = cache_path.read_bytes()
            meta = {}
            if self._meta_path(cache_path).exists():
                meta = json.loads(self._meta_path(cache_path).read_text(encoding="utf-8"))
            log.debug("cache hit %s", url)
            return FetchResult(
                url=url,
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                from_cache=True,
                local_path=cache_path,
                status=meta.get("status", 200),
                headers=meta.get("headers", {}),
            )

        if self.offline:
            raise FetchError(f"{url}: not in the cache and offline mode is on")

        self._wait_turn(url)
        log.info("GET %s", url)
        try:
            body, status, headers = self._opener(url, {"User-Agent": self.user_agent})
        except (urllib.error.URLError, OSError) as exc:
            raise FetchError(f"{url}: {exc}") from exc

        digest = hashlib.sha256(body).hexdigest()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(body)
        self._meta_path(cache_path).write_text(
            json.dumps({"url": url, "status": status, "headers": dict(headers)}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._record(url, digest, body, status, headers, cache_path)
        return FetchResult(
            url=url,
            body=body,
            sha256=digest,
            from_cache=False,
            local_path=cache_path,
            status=status,
            headers=dict(headers),
        )

    def _record(
        self, url: str, digest: str, body: bytes, status: int, headers: dict, path: Path
    ) -> None:
        if self.db is None:
            return
        self.db.record_fetch(
            url=url,
            sha256=digest,
            fetched_at=datetime.now(UTC).isoformat(),
            status=status,
            content_type=dict(headers).get("Content-Type"),
            headers=json.dumps(dict(headers), ensure_ascii=False),
            local_path=str(path),
            bytes=len(body),
        )
