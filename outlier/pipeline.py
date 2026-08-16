"""The run: one Instagram handle in, one gap report out.

    seed handle
      -> scrape profile, read its relatedProfiles
      -> scrape those, drop anyone outside 0.2x-5x the seed's followers
      -> rank the survivors by engagement rate, keep the top N
      -> in each peer's history, find posts that beat that peer's OWN median
      -> tear down those reels: frames, cut rhythm, transcript, vision model
      -> tear down the seed's own best posts the same way
      -> diff, and turn the difference into shootable concepts

Every expensive step is memoized in SQLite, so a second run over the same
handle is close to free and safe to iterate on.
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import brain, media, sources, store
from .metrics import (
    engagement,
    is_video,
    outliers,
    peers,
    profile_engagement_rate,
    rank_peers,
)

# Network-bound work, so threads are the right tool. Four at a time keeps us
# clear of VideoDB's indexing queue backing up, which is the slowest link.
WORKERS = 4

# Keyframes live here rather than in the run's temp dir so the dashboard can
# serve them from one static mount. Serving them from wherever the pipeline
# happened to write would mean accepting a filesystem path over HTTP.
FRAMES_DIR = Path(__file__).resolve().parent.parent / "data" / "frames"


def _log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _profile_summary(profile: dict, posts: list[dict]) -> dict:
    followers = int(profile.get("followersCount") or 0)
    return {
        "username": profile.get("username"),
        "fullName": profile.get("fullName"),
        "biography": profile.get("biography"),
        "followers": followers,
        "postsCount": profile.get("postsCount"),
        "engagementRate": round(profile_engagement_rate(posts, followers), 5),
        "medianEngagement": (
            int(sorted(engagement(p) for p in posts)[len(posts) // 2]) if posts else 0
        ),
    }


def _posts_for(username: str, profile: dict, deep: bool) -> list[dict]:
    """Post history for one account, deepest source available.

    The profile scraper caps at 12, which is too few for a stable median, so we
    pull a deeper history when a token is present and fall back to the embedded
    posts otherwise.
    """
    if deep and sources.has_token():
        return store.cached(f"posts:{username}", lambda: sources.fetch_posts(username, 24))
    return sources.normalize_posts(profile)


def _discover_peers(seed: str, profile: dict, posts: list[dict], manual: list[str]) -> list[str]:
    """Find candidate peer handles, cheapest source first.

    Instagram's relatedProfiles is the ideal source but it is only populated for
    some accounts. Measured against the live API: garyvee returns 49 related
    profiles, hubspot and thefutur return zero. Relying on it alone would mean
    the tool simply does not work for most accounts, so hashtag co-occurrence
    is the fallback: whoever ranks under the niche's own hashtags is the niche.
    """
    if manual:
        _log(f"using {len(manual)} manually supplied peers")
        return manual

    related = sources.related_usernames(profile)
    if len(related) >= 3:
        _log(f"{len(related)} related profiles from Instagram's graph")
        return related[:10]

    tags = sources.top_hashtags(posts, exclude=seed)
    source = "the account's own tags"

    if not tags and brain.has_key():
        # The account tags nothing topical. Its bio and captions still describe
        # the niche, so let the model name the tags that niche would use.
        tags = store.cached(
            f"inferred-tags:{seed}",
            lambda: brain.infer_hashtags(
                bio=profile.get("biography") or "",
                captions=[p.get("caption") or "" for p in posts],
                username=seed,
            ),
        )
        source = "tags inferred from bio and captions"

    if not tags:
        raise RuntimeError(
            f"@{seed} has no related profiles and no usable hashtags, so there is "
            f"nothing to compare it against. Pass peers explicitly:\n"
            f"  python -m outlier.cli run {seed} --peers handle1,handle2"
        )

    _log(f"no related profiles, using {source}: {', '.join('#' + t for t in tags)}")
    hashtag_posts = store.cached(
        f"hashtags:{seed}:{','.join(tags)}",
        lambda: sources.fetch_hashtag_posts(tags, limit=40),
    )

    # Rank by how many of the niche's top posts each account owns: showing up
    # repeatedly means consistently ranking, not one lucky post.
    counts: dict[str, int] = {}
    for p in hashtag_posts:
        owner = p.get("ownerUsername")
        if owner and owner.lower() != seed.lower():
            counts[owner] = counts.get(owner, 0) + 1
    ranked = [u for u, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]
    _log(f"{len(ranked)} candidate peers from hashtags")
    return ranked[:15]


def _teardown_reel(post: dict, workdir: Path) -> dict | None:
    """Frames plus rhythm plus transcript plus one vision call, for one reel.

    Returns None rather than raising: one dead CDN link should cost us that reel
    and nothing else. A partial teardown still beats aborting the run.
    """
    code = post.get("shortCode") or post.get("id") or "unknown"

    def produce() -> dict:
        url = post.get("videoUrl")
        if not url:
            raise RuntimeError("no videoUrl on post")

        path = media.download(url, workdir / f"{code}.mp4")
        rhythm = media.cut_rhythm(path)
        frames = media.keyframes(path, FRAMES_DIR / code)

        transcript = ""
        # VideoDB fetches by URL, so a local fixture reel has nothing to send.
        if media.has_videodb() and url.startswith("http"):
            try:
                transcript = media.transcript(url, f"outlier-{code}")
            except Exception as e:  # noqa: BLE001
                # Music-led reels genuinely have no speech. The frames still
                # carry the on-screen text, which is often the real hook.
                _log(f"    no transcript for {code}: {e}")

        analysis = {}
        if brain.has_key() and frames:
            analysis = brain.teardown(
                frames=frames,
                transcript=transcript,
                caption=post.get("caption") or "",
                rhythm=rhythm,
                lift=float(post.get("lift") or 1.0),
            )

        return {
            "shortCode": code,
            "url": post.get("url"),
            "lift": post.get("lift"),
            "likes": post.get("likesCount"),
            "comments": post.get("commentsCount"),
            "views": post.get("videoPlayCount") or post.get("videoViewCount"),
            "caption": (post.get("caption") or "")[:400],
            "rhythm": rhythm,
            "transcript": transcript[:2000],
            # Stored relative to FRAMES_DIR: absolute paths would break the
            # moment the repo moved, and the dashboard only needs the suffix.
            "frames": [f"{code}/{f.name}" for f in frames],
            **analysis,
        }

    try:
        return store.cached(f"teardown:{code}", produce)
    except Exception as e:  # noqa: BLE001
        _log(f"    skipped {code}: {e}")
        return None


def run(seed: str, *, peer_count: int = 5, per_peer: int = 2, deep: bool = True,
        manual_peers: list[str] | None = None) -> dict:
    seed = seed.lstrip("@").strip()
    workdir = Path(tempfile.mkdtemp(prefix=f"outlier-{seed}-"))
    _log(f"workdir {workdir}")

    # 1. The seed account.
    _log(f"scraping @{seed}")
    seed_profiles = store.cached(f"profile:{seed}", lambda: sources.fetch_profiles([seed]))
    if not seed_profiles:
        raise RuntimeError(f"No profile data for @{seed}")
    seed_profile = seed_profiles[0]
    seed_posts = _posts_for(seed, seed_profile, deep)
    seed_followers = int(seed_profile.get("followersCount") or 0)
    _log(f"@{seed}: {seed_followers} followers, {len(seed_posts)} posts")

    # 2. Its niche: related profiles if Instagram exposes them, hashtags if not.
    related = _discover_peers(seed, seed_profile, seed_posts, manual_peers or [])
    candidates = store.cached(
        f"profiles:{seed}:{','.join(sorted(related))[:120]}",
        lambda: sources.fetch_profiles(related),
    )

    # 3. Same weight class only, then ranked by how hard they convert.
    banded = peers(candidates, seed_followers)
    strict = [c for c in banded
              if seed_followers * 0.2 <= int(c.get("followersCount") or 0) <= seed_followers * 5]
    if len(strict) >= 3:
        _log(f"{len(banded)} of {len(candidates)} are in the same follower band")
    else:
        _log(f"only {len(strict)} candidates sit in the 0.2x-5x band, so comparing against "
             f"the {len(banded)} closest by size instead (looser comparison)")
    for c in banded:
        c["posts"] = _posts_for(c.get("username", ""), c, deep)

    # Hashtag discovery surfaces abandoned and spam accounts, which sit at zero
    # engagement and teach nothing. Live against @hubspot, four of five selected
    # peers were dead, so the whole "niche" comparison rested on one account.
    alive = [c for c in banded if any(engagement(p) for p in c["posts"])]
    if len(alive) < len(banded):
        _log(f"dropped {len(banded) - len(alive)} peers with no engagement at all")
    ranked = rank_peers(alive or banded, top_n=peer_count)
    _log("peers: " + ", ".join(f"@{p['username']} (ER {p['engagementRate']:.2%})" for p in ranked))

    # 4. Their breakout posts, and ours.
    jobs: list[tuple[str, dict]] = []
    for peer in ranked:
        for post in outliers(peer["posts"], limit=per_peer):
            if is_video(post):
                jobs.append((peer["username"], post))

    own_best = [p for p in sorted(seed_posts, key=engagement, reverse=True) if is_video(p)][:2]
    for post in own_best:
        jobs.append((seed, {**post, "lift": 1.0}))

    _log(f"tearing down {len(jobs)} reels")

    # 5. Teardowns, in parallel. Each is independent and self-contained.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda j: (j[0], _teardown_reel(j[1], workdir)), jobs))

    by_account: dict[str, list[dict]] = {}
    for username, td in results:
        if td:
            by_account.setdefault(username, []).append(td)
    _log(f"{sum(len(v) for v in by_account.values())} teardowns succeeded")

    you = {
        **_profile_summary(seed_profile, seed_posts),
        "teardowns": by_account.get(seed, []),
    }
    competitors = [
        {**_profile_summary(p, p["posts"]), "teardowns": by_account.get(p["username"], [])}
        for p in ranked
        if by_account.get(p["username"])
    ]

    # 6. The diff.
    report_body = {}
    if brain.has_key() and competitors:
        _log("writing gap report")
        report_body = brain.gap_report(you=you, competitors=competitors)
    elif not competitors:
        _log("no competitor teardowns survived, skipping report")

    report = {"seed": seed, "you": you, "competitors": competitors, **report_body}
    report["id"] = store.save_run(seed, report)
    _log(f"saved run {report['id']}")
    return report
