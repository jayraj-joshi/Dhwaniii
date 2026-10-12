import pytest
from app.text import normalize, normalize_query, parse_title


@pytest.mark.parametrize("fn,inp,out", [
    (normalize, "Beyoncé", "beyonce"),
    (normalize_query, "Tum Hi Ho (Official Video) HD", "tum hi ho"),
    (normalize, "Simon & Garfunkel", "simon and garfunkel"),
    (normalize, "तुम ही हो", "तुम ही हो"),
    (normalize_query, "audio", "audio"),
    (normalize, "Don't Stop  Me’s", "dont stop mes"),
    (normalize_query, "Kesariya Official Music Video 4K lyrics", "kesariya"),
    (normalize_query, "Official Video", "official video"),
])
def test_normalize(fn, inp, out):
    assert fn(inp) == out


def t(title, **kw):
    return parse_title({"title": title, **kw})


def test_doc_example():
    r = t("Arijit Singh - Tum Hi Ho (Official Video) | Aashiqui 2 | HD")
    assert (r["title"], r["artists"], r["album"]) == ("Tum Hi Ho", ["Arijit Singh"], "Aashiqui 2")


def test_simple_dash():
    r = t("Ed Sheeran - Shape of You (Official Music Video)")
    assert (r["title"], r["artists"], r["album"]) == ("Shape of You", ["Ed Sheeran"], None)


@pytest.mark.parametrize("sep", [" – ", " — "])
def test_dash_variants(sep):
    r = t(f"Coldplay{sep}Yellow")
    assert (r["title"], r["artists"]) == ("Yellow", ["Coldplay"])


def test_feat_bare_and_bracket():
    r = t("Post Malone - Rockstar ft. 21 Savage (Official Video)")
    assert (r["title"], r["artists"], r["featured"]) == ("Rockstar", ["Post Malone"], ["21 Savage"])
    r = t("Dua Lipa - Levitating (feat. DaBaby) [Official Lyric Video]")
    assert (r["title"], r["featured"]) == ("Levitating", ["DaBaby"])


def test_feat_in_artist_segment():
    r = t("Calvin Harris feat. Rihanna - This Is What You Came For")
    assert (r["artists"], r["featured"], r["title"]) == (["Calvin Harris"], ["Rihanna"], "This Is What You Came For")


def test_artist_list_split_on_comma_only():
    assert t("Dua Lipa, Elton John - Cold Heart")["artists"] == ["Dua Lipa", "Elton John"]
    assert t("Simon & Garfunkel - The Sound of Silence (Lyrics)")["artists"] == ["Simon & Garfunkel"]


def test_no_separator_uses_channel():
    assert t("Blank Space", channel="Taylor SwiftVEVO")["artists"] == ["Taylor Swift"]
    r = t("Kesariya (Official Video)", channel="Arijit Singh - Topic")
    assert (r["title"], r["artists"]) == ("Kesariya", ["Arijit Singh"])


def test_pipe_only_title():
    r = t("Tum Hi Ho | Aashiqui 2 | HD", channel="T-Series")
    assert (r["title"], r["artists"], r["album"]) == ("Tum Hi Ho", ["T-Series"], "Aashiqui 2")


def test_topic_description():
    d = "Provided to YouTube by Universal Music\n\nTum Hi Ho · Arijit Singh · Mithoon\n\nAashiqui 2\n\nReleased on: 2013-04-10\n"
    r = t("Tum Hi Ho", description=d)
    assert (r["title"], r["artists"], r["album"], r["year"]) == ("Tum Hi Ho", ["Arijit Singh", "Mithoon"], "Aashiqui 2", 2013)


def test_ytdlp_fields():
    r = t("x", track="Lover", artist="Diljit Dosanjh, Raj", album="MoonChild Era", release_year=2017)
    assert (r["title"], r["artists"], r["album"], r["year"]) == ("Lover", ["Diljit Dosanjh", "Raj"], "MoonChild Era", 2017)
    assert t("x", track="Lover", artists=["A", "B"])["artists"] == ["A", "B"]


def test_hint_from_ytmusicapi():
    r = t("Tum Hi Ho", artists=[{"name": "Arijit Singh", "ytm_channel_id": "UC1"}], album={"title": "Aashiqui 2"})
    assert (r["title"], r["artists"], r["album"]) == ("Tum Hi Ho", ["Arijit Singh"], "Aashiqui 2")


def test_all_noise_title_kept():
    assert t("Official Video (Official Video)", channel="X")["title"]
