import datetime

import pytest

from glean import settings

NOW = datetime.datetime(2026, 10, 22, 10, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))


@pytest.fixture(autouse=True)
def no_real_sift_or_orbit(monkeypatch):
    """Keep every test away from the sift and orbit of the machine it runs on: a test that wants one fakes it."""
    from glean import orbit, sift

    monkeypatch.setattr(sift.Sift, "find", classmethod(lambda cls, run=None: None))
    monkeypatch.setattr(orbit, "vault", lambda url, timeout=None: None)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A temporary GLEAN_HOME for every test, so no test touches real state, tokens or mail."""
    path = tmp_path / "home"
    monkeypatch.setenv("GLEAN_HOME", str(path))
    settings.current.cache_clear()
    yield path
    settings.current.cache_clear()
