"""Instagram data in, via Apify. No Instagram login anywhere in this file.

Two actors are used:
  apify/instagram-profile-scraper  profile stats + relatedProfiles + latestPosts
  apify/instagram-post-scraper     deeper post history for one account

Both are called through Apify's `run-sync-get-dataset-items` endpoint, which
blocks until the run finishes and returns the dataset inline. That keeps the
whole pipeline synchronous and debuggable, at the cost of a long HTTP wait.

Offline mode: with no APIFY_TOKEN set, every call reads from fixtures/ instead.
The pipeline is fully runnable with zero keys, which matters both for testing
and for anyone cloning the repo who wants to see it work before paying Apify.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

API = "https://api.apify.com/v2/acts"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

PROFILE_ACTOR = "apify~instagram-profile-scraper"
POST_ACTOR = "apify~instagram-post-scraper"
HASHTAG_ACTOR = "apify~instagram-hashtag-scraper"

# Apify runs are slow by nature (a real browser is scraping). Ten minutes is
# generous enough for a 6-account batch and still bounded.
TIMEOUT = 600


def has_token() -> bool:
    return bool(os.environ.get("APIFY_TOKEN"))


def _fixture(name: str) -> list[dict]:
    path = FIXTURES / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No APIFY_TOKEN set and no fixture at {path}. "
            f"Either export APIFY_TOKEN or run `python -m outlier.cli fixtures` first."
        )
    return json.loads(path.read_text())


SAMPLE_PREFIX = "sample."


def _is_sample(payload: dict) -> bool:
    """Whether this request is for the bundled sample dataset.

    Without this check, having an APIFY_TOKEN in the environment made
    `run sample.seed` hit the live API looking for an account named
    "sample.seed", get an error record back, and then go scrape whatever real
    accounts the inferred hashtags pointed at. Sample data must resolve to
    fixtures regardless of which keys happen to be set.
    """
    names = payload.get("usernames") or payload.get("username") or []
    if isinstance(names, str):
        names = [names]
    return bool(names) and all(str(n).startswith(SAMPLE_PREFIX) for n in names)


def _run(actor: str, payload: dict, fixture: str) -> list[dict]:
    if not has_token() or _is_sample(payload):
        return _fixture(fixture)

    # The token goes in a header, not the query string. As a query param it
    # ends up inside every requests HTTPError message, so any crash log,
    # screen-share or pasted traceback leaks the credential.
    res = requests.post(
        f"{API}/{actor}/run-sync-get-dataset-items",
        headers={"Authorization": f"Bearer {os.environ['APIFY_TOKEN']}"},
        json=payload,
        timeout=TIMEOUT,
    )
    res.raise_for_status()
    return res.json()


def fetch_profiles(usernames: list[str]) -> list[dict]:
    """Profile stats for each username, including relatedProfiles and up to 12
    latestPosts. One Apify call covers the whole list, so batching the peer set
    into a single call is both faster and cheaper than looping."""
    if not usernames:
        return []
    fixture = f"profiles_{usernames[0]}" if len(usernames) == 1 else "profiles_batch"
    return _run(PROFILE_ACTOR, {"usernames": usernames}, fixture)


def fetch_posts(username: str, limit: int = 24) -> list[dict]:
    """Deeper post history for one account.

    The profile scraper caps at 12 recent posts, which is too thin for a median
    to survive one viral post. This pulls more so outlier detection has ground
    to stand on.
    """
    return _run(
        POST_ACTOR,
        {"username": [username], "resultsLimit": limit},
        f"posts_{username}",
    )


def fetch_hashtag_posts(hashtags: list[str], limit: int = 30) -> list[dict]:
    """Top posts for a set of hashtags, used to discover peers.

    Instagram's own related-profile graph is only populated for some accounts
    (large ones, in practice), so this is the fallback that keeps peer discovery
    working for everyone else. Each returned post carries `ownerUsername`, and
    the accounts showing up under a niche's hashtags are that niche.
    """
    if not hashtags:
        return []
    return _run(
        HASHTAG_ACTOR,
        {"hashtags": hashtags, "resultsLimit": limit},
        f"hashtags_{hashtags[0]}",
    )


GENERIC_TAGS = {
    "reels", "reel", "viral", "explore", "explorepage", "fyp", "trending",
    "instagram", "instagood", "love", "follow", "like4like", "reelsinstagram",
    "instadaily", "photooftheday", "picoftheday", "repost", "follow4follow",
}

# Tags that describe the commercial relationship rather than the subject. On a
# live run against @hubspot these were the two most frequent tags on the
# account, and following them found sponsorship disclosures, not the niche.
NON_TOPICAL_TAGS = {"sponsored", "ad", "advertisement", "partner", "paidpartnership",
                    "giveaway", "collab", "gifted", "affiliate"}


def top_hashtags(posts: list[dict], limit: int = 3, exclude: str = "") -> list[str]:
    """The hashtags this account uses most that actually describe its subject.

    Three classes get dropped: platform-generic tags that would return the whole
    of Instagram, disclosure tags like #sponsored that describe a business
    arrangement, and the account's own branded tags, which by definition only
    that account and its partners use.
    """
    brand = exclude.replace(".", "").replace("_", "").lower()
    counts: dict[str, int] = {}
    for p in posts:
        for tag in p.get("hashtags") or []:
            # Strip everything that is not alphanumeric or underscore. A tag
            # ending a sentence, like "MetMoment.", is rejected by Apify with a
            # 400 and kills discovery for that account entirely.
            tag = re.sub(r"[^0-9a-z_]", "", str(tag).lower())
            if not tag or len(tag) <= 2:
                continue
            if tag in GENERIC_TAGS or tag in NON_TOPICAL_TAGS:
                continue
            if brand and brand in tag.replace("_", ""):
                continue
            counts[tag] = counts.get(tag, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def related_usernames(profile: dict) -> list[str]:
    """Pull peer handles out of a scraped profile.

    Apify has shipped this field under a couple of shapes and with either
    snake_case or camelCase keys depending on actor version, so we read
    defensively rather than trusting one spelling.
    """
    related = profile.get("relatedProfiles") or profile.get("related_profiles") or []
    out = []
    for r in related:
        name = r.get("username") if isinstance(r, dict) else r
        if name:
            out.append(str(name))
    return out


def normalize_posts(profile: dict) -> list[dict]:
    """The posts embedded in a profile record, under whichever key it used."""
    return profile.get("latestPosts") or profile.get("posts") or []


def save_fixture(name: str, data: list[dict]) -> Path:
    """Persist a live response so later runs (and the tests) work offline."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(data, indent=2))
    return path
