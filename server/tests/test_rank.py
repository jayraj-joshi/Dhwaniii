from app.youtube import rank_candidates


def c(vid, title, channel, secs, views, artist="Arijit Singh", ytm=False, title_artists=None):
    d = {"video_id": vid, "title": title, "channel": channel, "duration_ms": secs * 1000, "view_count": views,
         "artists": [{"name": artist}], "from_ytm": ytm}
    if title_artists is not None:
        d["title_artists"] = title_artists  # yt-dlp results: artists named in the title, not taken from the channel
    return d


def test_ranking_order():
    cands = [
        c("loop", "Tum Hi Ho 1 Hour Loop", "Loops", 3600, 2_000_000, artist="Loops", title_artists=[]),
        c("cover", "Tum Hi Ho (Cover)", "Random Singer", 260, 1_000_000, artist="Random Singer", title_artists=[]),
        c("live", "Tum Hi Ho (Live at Wembley)", "Arijit Singh", 300, 50_000, title_artists=[]),
        c("tseries", "Tum Hi Ho Full Video Song", "T-Series", 265, 500_000_000, title_artists=[]),
        c("topic", "Tum Hi Ho", "Arijit Singh - Topic", 262, 10_000_000, title_artists=[]),
    ]
    ranked = rank_candidates(cands, "tum hi ho arijit singh", ref_duration_ms=262_000)
    assert [x["video_id"] for x in ranked] == ["topic", "tseries", "cover", "live", "loop"]
    assert ranked[-1]["quality_score"] < -4


def test_penalty_skipped_when_query_asks_for_it():
    live = c("live", "Tum Hi Ho (Live)", "Someone", 260, 1000, artist="Someone")
    assert rank_candidates([live], "tum hi ho live")[0]["quality_score"] > rank_candidates([dict(live)], "tum hi ho")[0]["quality_score"]


def test_ytm_and_vevo_bonus_and_stable_ties():
    a, b = c("a", "Song", "X", 200, None, ytm=True), c("b", "Song", "Y", 200, None, ytm=True)
    assert [x["video_id"] for x in rank_candidates([a, b], "song")] == ["a", "b"]
    assert rank_candidates([c("v", "Song", "TaylorSwiftVEVO", 200, None, artist="Taylor Swift")], "song")[0]["quality_score"] == 3.0


def test_channel_matching_title_artist_counts():
    ok = c("ok", "Arijit Singh - Tum Hi Ho", "Arijit Singh", 262, None, title_artists=["Arijit Singh"])
    no = c("no", "Tum Hi Ho", "Arijit Singh", 262, None, title_artists=[])
    assert rank_candidates([ok], "x")[0]["quality_score"] == 3.0 and rank_candidates([no], "x")[0]["quality_score"] == 0.0
