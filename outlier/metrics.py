"""Pure ranking math: engagement rates, peer filtering, outlier detection.

No network, no I/O. Everything here is deterministic so the interesting part of
the pipeline stays testable without an API key (see test_outlier.py).

Vocabulary used throughout:
  engagement  = likes + comments on one post
  ER          = engagement / followers, i.e. how hard a post punched relative
                to the size of the account that posted it
  outlier     = a post that beat its OWN account's median by `threshold`x.
                This is the signal we care about: not "what is popular" but
                "what worked unusually well for this specific account", which
                is what isolates the creative choice from the follower count.
"""

from __future__ import annotations

from statistics import median
from typing import Iterable, Sequence


def engagement(post: dict) -> int:
    """Likes + comments. Missing counts are treated as zero, never as an error:
    Instagram hides like counts on some posts and we still want the row."""
    return int(post.get("likesCount") or 0) + int(post.get("commentsCount") or 0)


def engagement_rate(post: dict, followers: int) -> float:
    """Per-post ER. Returns 0.0 for follower-less accounts rather than dividing
    by zero, so a scrape that failed to read followers degrades to "unranked"
    instead of crashing the run."""
    if followers <= 0:
        return 0.0
    return engagement(post) / followers


def profile_engagement_rate(posts: Sequence[dict], followers: int) -> float:
    """Account-level ER, from the median post rather than the mean.

    Median on purpose: one viral post would drag a mean upward and make a
    mediocre account look like a peer worth studying.
    """
    if not posts or followers <= 0:
        return 0.0
    return median(engagement(p) for p in posts) / followers


def peers(
    candidates: Iterable[dict],
    seed_followers: int,
    *,
    low: float = 0.2,
    high: float = 5.0,
) -> list[dict]:
    """Keep only accounts in the same weight class as the seed.

    Instagram's "related profiles" happily hands back Nike when you ask about a
    12k-follower account. Comparing against an account 1000x your size teaches
    you nothing you can act on, so we band it: 0.2x to 5x the seed's followers.

    Candidates with unknown follower counts are dropped, not kept, because an
    unranked account would pollute the top-N selection below.
    """
    candidates = [c for c in candidates if int(c.get("followersCount") or 0) > 0]
    if seed_followers <= 0:
        return list(candidates)

    lo, hi = seed_followers * low, seed_followers * high
    banded = [c for c in candidates if lo <= int(c["followersCount"]) <= hi]
    if len(banded) >= 3:
        return banded

    # Hashtag-discovered peers are often much smaller than the seed, so a strict
    # band can empty the list and kill the run. Rather than report nothing, fall
    # back to the closest candidates by size ratio and let the caller say the
    # comparison is looser than ideal.
    #
    # The pool kept here is deliberately wider than the number of peers that end
    # up in the report. Ranking by engagement rate happens next, and it can only
    # discard dead accounts if it is given more candidates than it needs.
    def ratio(c: dict) -> float:
        f = int(c["followersCount"])
        return max(f, seed_followers) / min(f, seed_followers)

    return sorted(candidates, key=ratio)[:8]


def rank_peers(candidates: Sequence[dict], top_n: int = 5) -> list[dict]:
    """Order peers by account ER, strongest first.

    Each candidate is expected to carry `posts` (its recent posts) and
    `followersCount`. Ranking by ER rather than followers is the whole point:
    we want the accounts converting attention best in this niche, not the
    biggest ones.
    """
    scored = []
    for c in candidates:
        er = profile_engagement_rate(c.get("posts") or [], int(c.get("followersCount") or 0))
        scored.append({**c, "engagementRate": er})
    scored.sort(key=lambda c: c["engagementRate"], reverse=True)
    return scored[:top_n]


def outliers(posts: Sequence[dict], threshold: float = 2.0, limit: int = 3) -> list[dict]:
    """Posts that beat their own account's median engagement by `threshold`x.

    Returns them best-first with a `lift` field (how many times the median they
    hit). An account whose posts are all equally flat correctly yields nothing:
    there is no creative outlier to learn from there.
    """
    if len(posts) < 3:
        # Too few posts for a median to mean anything. Fall back to the single
        # best post so a thin account still contributes one example.
        best = sorted(posts, key=engagement, reverse=True)[:1]
        return [{**p, "lift": 1.0} for p in best]

    med = median(engagement(p) for p in posts)
    if med <= 0:
        return []

    hits = []
    for p in posts:
        lift = engagement(p) / med
        if lift >= threshold:
            hits.append({**p, "lift": round(lift, 2)})
    hits.sort(key=lambda p: p["lift"], reverse=True)
    return hits[:limit]


def is_video(post: dict) -> bool:
    """Whether this post has a video we can actually tear down."""
    return bool(post.get("videoUrl")) or (post.get("type") or "").lower() in {"video", "reel"}
