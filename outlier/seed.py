"""Load the committed sample runs into an empty database.

A freshly deployed instance has nothing in it, which is the worst possible first
impression for a tool whose entire pitch is "look at the evidence". This imports
the real runs in `samples/` so the dashboard explains itself the moment it
loads: real accounts, real peers, real keyframes, real measured cut rhythm.

It only ever runs against an empty `runs` table, so it can never overwrite work
someone actually did. Frames are copied rather than symlinked because the
dashboard serves `data/frames` as a static mount.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import store
from .pipeline import FRAMES_DIR

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# Which watchlist category each sample account is filed under, so the radar has
# all three populated rather than one.
CATEGORIES = {
    "mds": "learning",
    "codetourig": "learning",
    "startups": "aspiration",
    "therealfooddietitians": "competitor",
    "kitchenathoskins": "competitor",
    "livingsweetmoments": "competitor",
    "fabeveryday": "competitor",
    "_socialjack_": "competitor",
    "jjluizgomes": "aspiration",
}


def _copy_frames(report: dict) -> None:
    for teardown in _teardowns(report):
        for rel in teardown.get("frames") or []:
            src = SAMPLES / "frames" / rel
            if not src.exists():
                continue
            dst = FRAMES_DIR / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)


def _teardowns(report: dict) -> list[dict]:
    out = list((report.get("you") or {}).get("teardowns") or [])
    for competitor in report.get("competitors") or []:
        out.extend(competitor.get("teardowns") or [])
    return out


def seed_if_empty() -> int:
    """Import the sample runs if there are none. Returns how many were added."""
    if store.list_runs(limit=1):
        return 0
    if not SAMPLES.exists():
        return 0

    added = 0
    for path in sorted(SAMPLES.glob("report-*.json")):
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not _teardowns(report):
            continue

        _copy_frames(report)
        store.save_run(report.get("seed") or path.stem, report)
        added += 1

        # Peers become the watchlist, and their breakouts become the feed, so
        # every section of the dashboard has something in it rather than three
        # empty-state cards.
        for competitor in report.get("competitors") or []:
            username = competitor.get("username")
            if not username:
                continue
            category = CATEGORIES.get(username, "competitor")
            store.watch_add(username, category)
            posts = competitor.get("posts") or []
            store.add_snapshot(
                username,
                followers=int(competitor.get("followers") or 0),
                median_eng=int(competitor.get("medianEngagement") or 0),
                eng_rate=float(competitor.get("engagementRate") or 0.0),
                post_count=len(posts) or int(competitor.get("postsCount") or 0),
            )
            for teardown in competitor.get("teardowns") or []:
                code = teardown.get("shortCode")
                if code:
                    store.add_breakout(
                        code, username, category,
                        float(teardown.get("lift") or 1.0), teardown,
                    )

    return added


if __name__ == "__main__":
    print(f"seeded {seed_if_empty()} sample runs")

