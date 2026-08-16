"""Self-check for the logic that decides what gets analyzed.

Run: python test_outlier.py

Covers the pure ranking math and the response parsing, the two places where a
silent bug would send the pipeline off to tear down the wrong reels and nobody
would notice, because the output would still look like a plausible report.
Network paths are not covered here on purpose; `cli.py fixtures` exercises those.
"""

from outlier.brain import _json
from outlier.metrics import (
    engagement,
    engagement_rate,
    is_video,
    outliers,
    peers,
    profile_engagement_rate,
    rank_peers,
)
from outlier.track import digest_markdown


def post(likes, comments=0, **extra):
    return {"likesCount": likes, "commentsCount": comments, **extra}


def test_engagement():
    assert engagement(post(100, 20)) == 120
    assert engagement({}) == 0, "missing counts must not raise"
    assert engagement_rate(post(100, 0), 1000) == 0.1
    assert engagement_rate(post(100), 0) == 0.0, "no division by zero on unknown followers"


def test_profile_rate_uses_median_not_mean():
    # Nine flat posts and one 100x viral post. A mean would call this account a
    # strong performer; the median correctly says it is not.
    flat = [post(100) for _ in range(9)] + [post(10_000)]
    assert profile_engagement_rate(flat, 10_000) == 0.01
    assert profile_engagement_rate([], 10_000) == 0.0


def test_peers_bands_by_size():
    seed = 10_000
    candidates = [
        {"username": "tiny", "followersCount": 500},        # 0.05x, out
        {"username": "peer", "followersCount": 12_000},     # 1.2x, in
        {"username": "big", "followersCount": 40_000},      # 4x, in
        {"username": "mid", "followersCount": 8_000},       # 0.8x, in
        {"username": "nike", "followersCount": 5_000_000},  # 500x, out
        {"username": "unknown"},                            # no count, always out
    ]
    kept = {c["username"] for c in peers(candidates, seed)}
    assert kept == {"peer", "big", "mid"}, kept
    # An unknown follower count is never usable, even with no seed size to band by.
    assert "unknown" not in {c["username"] for c in peers(candidates, 0)}


def test_peers_falls_back_when_band_is_empty():
    # Hashtag-discovered peers are routinely far smaller than the seed. A strict
    # band would return nothing and the run would die with no comparison at all,
    # which is what happened on the first live run against @hubspot.
    seed = 656_000
    candidates = [
        {"username": "small_a", "followersCount": 4_000},
        {"username": "small_b", "followersCount": 12_000},
        {"username": "small_c", "followersCount": 900},
    ]
    kept = [c["username"] for c in peers(candidates, seed)]
    assert kept, "must not return an empty peer set"
    assert kept[0] == "small_b", "closest by size ratio comes first"


def test_rank_peers_orders_by_engagement_rate_not_size():
    small_but_hot = {"username": "hot", "followersCount": 1_000,
                     "posts": [post(100) for _ in range(5)]}       # ER 0.10
    big_but_cold = {"username": "cold", "followersCount": 100_000,
                    "posts": [post(500) for _ in range(5)]}        # ER 0.005
    ranked = rank_peers([big_but_cold, small_but_hot])
    assert [p["username"] for p in ranked] == ["hot", "cold"]
    assert ranked[0]["engagementRate"] == 0.1


def test_outliers_are_relative_to_own_median():
    posts = [post(100) for _ in range(9)] + [post(500, shortCode="viral")]
    hits = outliers(posts)
    assert len(hits) == 1 and hits[0]["shortCode"] == "viral"
    assert hits[0]["lift"] == 5.0

    # A uniformly flat account has no creative outlier to learn from.
    assert outliers([post(100) for _ in range(10)]) == []

    # Too few posts for a meaningful median: fall back to the single best.
    thin = outliers([post(10), post(90)])
    assert len(thin) == 1 and thin[0]["likesCount"] == 90

    assert outliers([post(0) for _ in range(5)]) == [], "zero median must not divide"


def test_outliers_respect_limit_and_order():
    posts = [post(100) for _ in range(6)] + [
        post(1000, shortCode="a"), post(600, shortCode="b"), post(400, shortCode="c")
    ]
    hits = outliers(posts, limit=2)
    assert [h["shortCode"] for h in hits] == ["a", "b"], "best-first, then truncate"


def test_is_video():
    assert is_video({"videoUrl": "http://x/y.mp4"})
    assert is_video({"type": "Video"})
    assert not is_video({"type": "Image"})


def test_json_survives_model_prose():
    assert _json('Sure! ```json\n{"a": 1}\n```') == {"a": 1}
    assert _json("no json here") == {}
    assert _json('{"broken": ') == {}, "malformed JSON degrades to empty, never raises"


def test_digest_renders_without_optional_fields():
    # A teardown that failed its vision call has no hookLine. The digest must
    # still render rather than KeyError during a scheduled run.
    digest = {
        "tracked": 1,
        "breakouts": [{"username": "a", "category": "learning", "lift": 3.0,
                       "rhythm": {"cuts": 8, "durationSec": 14.0, "avgShotSec": 1.6}}],
        "movements": [{"username": "a", "category": "learning", "followers": 1000,
                       "engagementRate": 0.05, "trend": None}],
    }
    text = digest_markdown(digest)
    assert "3.0x their median" in text
    assert "first capture" in text, "no trend on first snapshot, and we say so"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
