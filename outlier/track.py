"""Standing surveillance of a categorized watchlist.

The one-shot teardown answers "why am I behind right now". Tracking answers the
question you actually have every week: "what just started working in my niche,
and who is pulling away from me".

Each pass over the watchlist:
  1. re-scrapes every watched account (deliberately uncached, freshness is the
     entire product here)
  2. writes a snapshot so follower and engagement-rate trends accumulate
  3. finds posts that broke out against that account's own median
  4. tears down only the breakouts it has never seen before
  5. emits a digest of what is new

Step 4 is what keeps this affordable. A watchlist of 20 accounts checked daily
would be ruinous if every pass re-analyzed every post; in practice a niche
produces a handful of genuine breakouts a week, and those are the only reels
that get downloaded and sent to a vision model.

Categories are free-form strings. The ones that have earned their keep so far:
  learning     educators who teach the craft, watched for technique
  competitor   accounts fighting you for the same follower
  aspiration   accounts several tiers above, watched for where the niche goes
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import sources, store
from .metrics import engagement, is_video, outliers, profile_engagement_rate
from .pipeline import WORKERS, _log, _teardown_reel


def _median_engagement(posts: list[dict]) -> int:
    if not posts:
        return 0
    ordered = sorted(engagement(p) for p in posts)
    return int(ordered[len(ordered) // 2])


def track(category: str | None = None, *, per_account: int = 2) -> dict:
    """One tracking pass. Returns the digest."""
    watched = store.watchlist(category)
    if not watched:
        raise RuntimeError(
            "Watchlist is empty. Add accounts first:\n"
            "  python -m outlier.cli watch add <handle> --category learning"
        )

    usernames = sorted({w["username"] for w in watched})
    category_of = {w["username"]: w["category"] for w in watched}
    _log(f"tracking {len(usernames)} accounts")

    # Uncached on purpose. Everywhere else in this codebase caching is the right
    # call; here it would make the feature a no-op.
    profiles = sources.fetch_profiles(usernames)
    seen = store.known_breakouts()

    movements: list[dict] = []
    jobs: list[tuple[str, dict]] = []

    for profile in profiles:
        username = profile.get("username")
        if not username:
            continue
        posts = (
            sources.fetch_posts(username, 24)
            if sources.has_token()
            else sources.normalize_posts(profile)
        )
        followers = int(profile.get("followersCount") or 0)

        store.add_snapshot(
            username,
            followers=followers,
            median_eng=_median_engagement(posts),
            eng_rate=profile_engagement_rate(posts, followers),
            post_count=len(posts),
        )
        movements.append({
            "username": username,
            "category": category_of.get(username, "uncategorized"),
            "followers": followers,
            "engagementRate": round(profile_engagement_rate(posts, followers), 5),
            "trend": store.trend(username),
        })

        fresh = [
            p for p in outliers(posts, limit=per_account)
            if is_video(p) and (p.get("shortCode") or p.get("id")) not in seen
        ]
        for post in fresh:
            jobs.append((username, post))

    _log(f"{len(jobs)} new breakouts to tear down")

    workdir = Path(tempfile.mkdtemp(prefix="outlier-track-"))
    new_breakouts: list[dict] = []
    if jobs:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(lambda j: (j[0], _teardown_reel(j[1], workdir)), jobs))
        for username, td in results:
            if not td:
                continue
            store.add_breakout(
                td["shortCode"], username, category_of.get(username, "uncategorized"),
                float(td.get("lift") or 1.0), td,
            )
            new_breakouts.append({**td, "username": username,
                                  "category": category_of.get(username, "uncategorized")})

    # Biggest movers first: that is what a person actually scans for.
    movements.sort(key=lambda m: abs((m["trend"] or {}).get("followersPct", 0)), reverse=True)
    new_breakouts.sort(key=lambda b: b.get("lift") or 0, reverse=True)

    return {"tracked": len(usernames), "movements": movements, "breakouts": new_breakouts}


def digest_markdown(digest: dict) -> str:
    """Human-readable summary of one pass, for a terminal or an email body."""
    lines = [f"# Outlier digest", "", f"Tracked {digest['tracked']} accounts.", ""]

    if digest["breakouts"]:
        lines += ["## New breakouts", ""]
        for b in digest["breakouts"]:
            lines.append(f"### @{b['username']} ({b['category']}) — {b.get('lift')}x their median")
            if b.get("hookLine"):
                lines.append(f"- Hook: \"{b['hookLine']}\" ({b.get('hookTechnique', '?')})")
            if b.get("rhythm"):
                r = b["rhythm"]
                lines.append(
                    f"- Pacing: {r.get('cuts')} cuts in {r.get('durationSec')}s "
                    f"(avg shot {r.get('avgShotSec')}s)"
                )
            if b.get("replicable"):
                lines.append(f"- Steal this: {b['replicable']}")
            if b.get("url"):
                lines.append(f"- {b['url']}")
            lines.append("")
    else:
        lines += ["## New breakouts", "", "None this pass.", ""]

    lines += ["## Movement", ""]
    for m in digest["movements"]:
        t = m["trend"]
        delta = f"{t['followersPct']:+.2f}% followers" if t else "first capture"
        lines.append(
            f"- @{m['username']} ({m['category']}): {m['followers']:,} followers, "
            f"ER {m['engagementRate']:.2%}, {delta}"
        )
    return "\n".join(lines) + "\n"
