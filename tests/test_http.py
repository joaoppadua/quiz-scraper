"""The fetch layer: url rewriting, caching, rate limiting, provenance. No network."""

import hashlib
import json

import pytest

from bqpp.db import Database
from bqpp.harvest.http import Fetcher, normalise_url


class FakeOpener:
    """Stands in for urllib. Records every call it is actually asked to make."""

    def __init__(self, body: bytes = b"%PDF-1.7 hello", status: int = 200) -> None:
        self.body, self.status = body, status
        self.calls: list[str] = []

    def __call__(self, url: str, headers: dict) -> tuple[bytes, int, dict]:
        self.calls.append(url)
        return self.body, self.status, {"Content-Type": "application/pdf"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()


def _fetcher(tmp_path, opener, db=None, clock=None, min_interval=1.5):
    clock = clock or FakeClock()
    return Fetcher(
        user_agent="bqpp-test/0.1",
        cache_dir=tmp_path / "cache",
        db=db,
        min_interval=min_interval,
        opener=opener,
        sleep=clock.sleep,
        clock=clock.monotonic,
    )


# ---- url normalisation (spec amendment B3) ---------------------------------

def test_http_urls_are_upgraded_to_https():
    """The OAB index emits http:// for 67% of links, and those 502."""
    assert normalise_url("http://s.oab.org.br/arquivos/x.pdf") == "https://s.oab.org.br/arquivos/x.pdf"


def test_https_urls_are_left_alone():
    assert normalise_url("https://s.oab.org.br/x.pdf") == "https://s.oab.org.br/x.pdf"


def test_fetch_rewrites_before_requesting(tmp_path):
    opener = FakeOpener()
    _fetcher(tmp_path, opener).get("http://s.oab.org.br/x.pdf")
    assert opener.calls == ["https://s.oab.org.br/x.pdf"]


# ---- caching ---------------------------------------------------------------

def test_second_get_is_served_from_cache(tmp_path):
    opener = FakeOpener()
    f = _fetcher(tmp_path, opener)
    first = f.get("https://s.oab.org.br/x.pdf")
    second = f.get("https://s.oab.org.br/x.pdf")

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.body == first.body
    assert opener.calls == ["https://s.oab.org.br/x.pdf"], "must not re-download an unchanged file"


def test_cache_survives_a_new_fetcher(tmp_path):
    """The cache is on disk, so a later run of the CLI reuses it."""
    opener = FakeOpener()
    _fetcher(tmp_path, opener).get("https://s.oab.org.br/x.pdf")
    again = _fetcher(tmp_path, opener).get("https://s.oab.org.br/x.pdf")
    assert again.from_cache is True
    assert len(opener.calls) == 1


def test_cache_is_keyed_by_url_not_filename(tmp_path):
    opener = FakeOpener()
    f = _fetcher(tmp_path, opener)
    f.get("https://s.oab.org.br/a/x.pdf")
    f.get("https://s.oab.org.br/b/x.pdf")
    assert len(opener.calls) == 2, "same basename, different URL -> different cache entries"


def test_sha256_is_the_digest_of_the_body(tmp_path):
    opener = FakeOpener(body=b"some bytes")
    r = _fetcher(tmp_path, opener).get("https://s.oab.org.br/x.pdf")
    assert r.sha256 == hashlib.sha256(b"some bytes").hexdigest()


def test_cache_hit_reports_the_same_digest(tmp_path):
    opener = FakeOpener(body=b"some bytes")
    f = _fetcher(tmp_path, opener)
    assert f.get("https://s.oab.org.br/x.pdf").sha256 == f.get("https://s.oab.org.br/x.pdf").sha256


# ---- rate limiting (spec §6: <= 1 request/second per host) -----------------

def test_consecutive_requests_to_one_host_are_spaced(tmp_path):
    clock, opener = FakeClock(), FakeOpener()
    f = _fetcher(tmp_path, opener, clock=clock, min_interval=1.5)
    f.get("https://s.oab.org.br/a.pdf")
    f.get("https://s.oab.org.br/b.pdf")
    assert clock.slept and clock.slept[0] >= 1.5


def test_different_hosts_do_not_block_each_other(tmp_path):
    clock, opener = FakeClock(), FakeOpener()
    f = _fetcher(tmp_path, opener, clock=clock)
    f.get("https://s.oab.org.br/a.pdf")
    f.get("https://examedeordem.oab.org.br/b")
    assert clock.slept == []


def test_a_cache_hit_costs_no_delay(tmp_path):
    clock, opener = FakeClock(), FakeOpener()
    f = _fetcher(tmp_path, opener, clock=clock)
    f.get("https://s.oab.org.br/a.pdf")
    f.get("https://s.oab.org.br/a.pdf")
    assert clock.slept == [], "we are not being impolite to our own disk"


# ---- provenance manifest (spec §6) ----------------------------------------

def test_every_download_is_recorded_in_the_manifest(tmp_path, db):
    opener = FakeOpener(body=b"payload")
    f = _fetcher(tmp_path, opener, db=db)
    f.get("http://s.oab.org.br/x.pdf")

    rows = list(db.conn.execute("SELECT * FROM harvest_manifest"))
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://s.oab.org.br/x.pdf", "the manifest records what we actually fetched"
    assert row["sha256"] == hashlib.sha256(b"payload").hexdigest()
    assert row["bytes"] == len(b"payload")
    assert row["status"] == 200
    assert row["fetched_at"]
    assert json.loads(row["headers"])["Content-Type"] == "application/pdf"
    assert row["local_path"]


def test_a_cache_hit_does_not_add_a_manifest_row(tmp_path, db):
    f = _fetcher(tmp_path, FakeOpener(), db=db)
    f.get("https://s.oab.org.br/x.pdf")
    f.get("https://s.oab.org.br/x.pdf")
    assert db.conn.execute("SELECT COUNT(*) FROM harvest_manifest").fetchone()[0] == 1


def test_fetching_without_a_database_still_works(tmp_path):
    """harvest --dry-run and the parse-only path have no db handle."""
    assert _fetcher(tmp_path, FakeOpener()).get("https://s.oab.org.br/x.pdf").body
