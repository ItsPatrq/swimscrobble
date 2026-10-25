from pathlib import Path

from swimscrobble import acquire, history
from swimscrobble.config import Config
from swimscrobble.library import Track, scan
from swimscrobble.libsync import sync_library
from swimscrobble.link import LocalIndex
from swimscrobble.ytm import Entry

NOW = 100 * 86400.0


def T(artist, title, dur=200, name=None):
    return Track(Path(name or f"{artist}-{title}.mp3"), artist, title, "", dur)


def test_index_tolerates_noise_and_credits():
    idx = LocalIndex([T("Depeche Mode, War Child Records", "Universal Soldier"), T("Closterkeller", "Białe wino")])
    assert idx.find("Depeche Mode", "Universal Soldier (Remastered)")
    assert idx.find("Closterkeller", "Biale wino [MIXED]")
    assert idx.find("Someone Else", "Universal Soldier") is None
    assert idx.find("", "Universal Soldier")  # missing artist tag in playlist data


def test_index_prefers_closest_duration_and_rejects_other_cut():
    idx = LocalIndex([T("A", "S", 200, "studio.mp3"), T("A", "S", 400, "live.mp3")])
    assert idx.find("A", "S", 205).path.name == "studio.mp3"
    assert idx.find("A", "S", 395).path.name == "live.mp3"
    assert LocalIndex([T("A", "S", 200)]).find("A", "S", 900) is None


def test_scan_is_recursive_and_filters(tmp_path):
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        return
    for rel, dur in [("YouTubeMusic/Alb/01 - a.mp3", 70), ("Logic/loop.mp3", 70), ("x/short.mp3", 5), (".hidden/h.mp3", 70)]:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=duration={dur}", str(f)], check=True)
    (tmp_path / "x" / "notes.txt").write_text("hi")
    found = [t.path.relative_to(tmp_path).as_posix() for t in scan(tmp_path, exclude=("Logic",), min_seconds=60)]
    assert found == ["YouTubeMusic/Alb/01 - a.mp3"]


def make_cfg(tmp_path):
    return Config(music_dir=str(tmp_path), ytm_playlist="https://music.youtube.com/playlist?list=X", lastfm_user="me")


def test_sync_reports_missing_and_downloads_then_rescans(tmp_path):
    db = history.connect(tmp_path / "h.db")
    cfg = make_cfg(tmp_path)
    lib = [T("Depeche Mode", "Free")]
    entries = [Entry("1", "Depeche Mode", "Free", 200, 1), Entry("2", "Depeche Mode", "Newborn", 200, 2),
               Entry("3", "Depeche Mode", "Stripped", 200, 3)]
    got = []

    def download(missing):
        got.append([e.index for e in missing])
        lib.extend([T("Depeche Mode", "Newborn"), T("Depeche Mode", "Stripped")])

    rep = sync_library(cfg, db, fetch_playlist=lambda url: entries, library_loader=lambda: list(lib), download=download)
    assert got == [[2, 3]] and rep.downloaded and rep.playlist_missing == []
    assert len(rep.pool.added) == 3

    lib2 = [T("Depeche Mode", "Free")]
    rep = sync_library(cfg, db, fetch_playlist=lambda url: entries, library_loader=lambda: list(lib2))
    assert [e.index for e in rep.playlist_missing] == [2, 3] and not rep.downloaded  # report only


def test_download_failure_leaves_entries_missing(tmp_path):
    db = history.connect(tmp_path / "h.db")
    rep = sync_library(make_cfg(tmp_path), db, fetch_playlist=lambda u: [Entry("1", "A", "Gone", 100, 1)],
                       library_loader=lambda: [], download=lambda m: None)
    assert [e.index for e in rep.playlist_missing] == [1] and rep.downloaded


def test_pool_is_the_playlist_and_history_decides_buckets(tmp_path):
    from swimscrobble.taste import Taste

    db = history.connect(tmp_path / "h.db")
    db.executemany("INSERT INTO scrobbles VALUES(?,?,?)", [(int(NOW) - 86400, "Closterkeller", "Biale wino")] * 1)
    db.commit()
    lib = [T("Closterkeller", "Białe wino"), T("NIN", "Closer"), T("Not In Playlist", "Hidden")]
    entries = [Entry("1", "Closterkeller", "Białe wino", 200, 1), Entry("2", "NIN", "Closer", 200, 2)]
    seeds = []
    taste = lambda user, tracks, artists: (seeds.append((tracks, artists)) or Taste())  # noqa: E731
    cfg = make_cfg(tmp_path)
    cfg.ytm_playlists = ["https://music.youtube.com/playlist?list=Y"]
    syncs = []
    rep = sync_library(cfg, db, fetch_playlist=lambda u: [e for e in entries], taste_fetch=taste,
                       history_sync=lambda: syncs.append(1), library_loader=lambda: lib, now=NOW)
    pool = {(r["artist"], r["title"]): r["source"] for r in db.execute("SELECT artist, title, source FROM pool")}
    assert pool == {("closterkeller", "białe wino"): "obsession", ("nin", "closer"): "rec"}  # keyed by LOCAL tags
    assert ("not in playlist", "hidden") not in pool  # outside the playlist: not eligible
    assert syncs == [1] and seeds == [([("Closterkeller", "Biale wino")], ["Closterkeller"])]
    assert rep.playlist_total == 4  # two playlists configured (ytm_playlist + ytm_playlists), 2 entries each

    cfg.pool = "library"
    sync_library(cfg, db, fetch_playlist=lambda u: entries, library_loader=lambda: lib, now=NOW)
    assert ("not in playlist", "hidden") in {(r["artist"], r["title"]) for r in db.execute("SELECT artist, title FROM pool")}


def test_download_command_is_the_ymp3_alias_limited_to_missing_items(tmp_path):
    cfg = Config(download_dir=str(tmp_path / "dl"), cookies_file="~/cookies.txt")
    cmd = acquire.playlist_download_command(cfg, "https://pl", [7, 2, 12])
    assert cmd[0] == "yt-dlp" and cmd[1:3] == ["--cookies", str(Path("~/cookies.txt").expanduser())]
    assert "--embed-thumbnail" in cmd and "playlist_index:%(track_number)s" in cmd
    assert cmd[-3:] == ["--playlist-items", "2,7,12", "https://pl"][-3:] or cmd[-1] == "https://pl"
    i = cmd.index("--playlist-items")
    assert cmd[i + 1] == "2,7,12" and cmd[-1] == "https://pl"
    assert any(str(tmp_path / "dl") in part for part in cmd)  # --output points at download_dir


def test_download_missing_asks_and_reports(tmp_path):
    cfg = Config(download_dir=str(tmp_path))
    ran = []
    class R:
        returncode = 0
    run = lambda cmd: (ran.append(cmd) or R())  # noqa: E731
    es = [Entry("1", "A", "T", 1, 4)]
    assert acquire.download_missing(cfg, "u", es, run, ask=lambda _: "n") is False and not ran
    assert acquire.download_missing(cfg, "u", es, run, ask=lambda _: "y") and ran[0][-3:-1] == ["--playlist-items", "4"]
    assert acquire.download_missing(cfg, "u", [], run) is True
