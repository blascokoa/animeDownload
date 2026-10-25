from datetime import timedelta

from animedl.db import Database
from animedl.models import EpisodeRef, EpisodeStatus, Monitor, SeriesStatus


def _series(db: Database, status=SeriesStatus.AIRING, monitor=Monitor.AUTO):
    return db.add_series(
        url="https://jkanime.net/x/",
        site="jkanime",
        title="X",
        folder="X",
        status=status,
        monitor=monitor,
    )


def test_upsert_returns_only_new_episodes(db):
    s = _series(db)
    refs = [EpisodeRef(1, "https://jkanime.net/x/1/"), EpisodeRef(2, "https://jkanime.net/x/2/")]
    assert len(db.upsert_episodes(s.id, refs)) == 2
    assert db.upsert_episodes(s.id, refs) == []
    new = db.upsert_episodes(s.id, [*refs, EpisodeRef(3, "https://jkanime.net/x/3/")])
    assert [e.number for e in new] == [3]


def test_upsert_refreshes_urls_when_domain_changes(db):
    s = _series(db)
    db.upsert_episodes(s.id, [EpisodeRef(1, "https://old.net/x/1/")])
    db.upsert_episodes(s.id, [EpisodeRef(1, "https://new.net/x/1/")])
    [ep] = db.episodes(s.id)
    assert ep.url == "https://new.net/x/1/"


def test_claim_is_exclusive_and_failures_count(db):
    s = _series(db)
    [ep] = db.upsert_episodes(s.id, [EpisodeRef(1, "u1")])
    assert db.claim_episode(ep.id)
    assert not db.claim_episode(ep.id)
    db.mark_failed(ep.id, "boom")
    ep = db.get_episode(ep.id)
    assert ep.status == EpisodeStatus.FAILED and ep.attempts == 1 and ep.last_error == "boom"
    assert db.claim_episode(ep.id)
    db.release(ep.id)
    ep = db.get_episode(ep.id)
    assert ep.status == EpisodeStatus.PENDING and ep.attempts == 1


def test_reset_stale_downloads(db):
    s = _series(db)
    [ep] = db.upsert_episodes(s.id, [EpisodeRef(1, "u1")])
    db.claim_episode(ep.id)
    assert db.reset_stale_downloads(timedelta(hours=1)) == 0
    assert db.reset_stale_downloads(timedelta(seconds=-1)) == 1
    assert db.get_episode(ep.id).status == EpisodeStatus.PENDING


def test_should_check_rules(db):
    assert _series(db, SeriesStatus.AIRING).should_check
    db.conn.execute("DELETE FROM series")
    assert not _series(db, SeriesStatus.FINISHED).should_check
    db.conn.execute("DELETE FROM series")
    assert _series(db, SeriesStatus.FINISHED, Monitor.ALWAYS).should_check
    db.conn.execute("DELETE FROM series")
    assert not _series(db, SeriesStatus.AIRING, Monitor.NEVER).should_check
    db.conn.execute("DELETE FROM series")
    assert _series(db, SeriesStatus.UNKNOWN).should_check


def test_find_series(db):
    s = _series(db)
    assert db.find_series(str(s.id))[0].id == s.id
    assert db.find_series("https://jkanime.net/x/")[0].id == s.id
    assert db.find_series("x")[0].id == s.id
    assert db.find_series("nada") == []


def test_reopen_keeps_data(settings):
    with Database(settings.database) as db:
        s = _series(db)
        db.upsert_episodes(s.id, [EpisodeRef(1, "u1")])
    with Database(settings.database) as db:
        assert len(db.episodes(s.id)) == 1
