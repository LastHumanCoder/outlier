"""Command line entry.

    python -m outlier.cli fixtures                       build the offline sample dataset
    python -m outlier.cli run <handle>                   one-shot teardown vs the niche
    python -m outlier.cli watch add <handle> [category]  track an account
    python -m outlier.cli watch rm <handle> [category]   stop tracking
    python -m outlier.cli watch list                     show the watchlist
    python -m outlier.cli track [category]               one tracking pass, prints a digest
    python -m outlier.cli serve                          dashboard on :8000

Categories are free-form. `learning`, `competitor` and `aspiration` are the
ones the tracker's digest is written around.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

from . import pipeline, sources, store, track

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env() -> None:
    """Read .env into the environment if present.

    The README tells people to create a .env, so it has to actually be read.
    Doing it by hand rather than adding python-dotenv keeps the dependency
    list at three packages. Real environment variables always win.
    """
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

VIDEO_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "videos"

FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]

# (username, followers, engagement rate, seconds per shot)
# The last field is the point of the fixture set: peer.one cuts every 1.2s and
# peer.four sits on a single shot for 9s, so the pacing gap the report claims to
# find is really there in the pixels and can be measured, not asserted.
FIXTURE_ACCOUNTS = [
    ("sample.seed", 12_400, 0.02, 4.5),
    ("sample.peer.one", 18_900, 0.09, 1.2),
    ("sample.peer.two", 9_600, 0.07, 1.8),
    ("sample.peer.three", 41_000, 0.05, 2.4),
    ("sample.peer.four", 6_200, 0.01, 9.0),
    ("sample.toobig", 4_100_000, 0.06, 2.0),
]

CAPTIONS = [
    "the mistake everyone makes in their first 30 days",
    "3 things I wish someone told me sooner",
    "stop doing this if you want results",
    "watch this before you buy anything else",
    "nobody talks about this part",
    "I tried it for 60 days, here is what happened",
]

# Shown on screen, one card per shot. The first line is the hook, which is what
# the vision model should be reading off frame one.
SCRIPTS = [
    ["STOP scrolling", "You are doing this wrong", "Here is the fix", "Step 1", "Step 2",
     "Step 3", "That is it", "Follow for more"],
    ["I lost 40k followers", "Then I changed ONE thing", "Watch what happened", "Day 1",
     "Day 30", "Day 60", "Save this"],
    ["Nobody tells you this", "The algorithm does not care", "It cares about THIS",
     "Try it today", "Comment GUIDE"],
]

FONT_SIZE = 54


def _font() -> str:
    for f in FONTS:
        if Path(f).exists():
            return f
    raise RuntimeError(f"No usable font found, looked in: {FONTS}")


def _make_video(dest: Path, script: list[str], shot_sec: float) -> Path:
    """Synthesize one vertical fixture reel with real hard cuts.

    Each line of `script` becomes one flat-colour shot with the text burned in.
    Cutting between distinct colours guarantees ffmpeg's scene detector sees the
    cut, so cut_rhythm() measures exactly what we intended to encode.
    """
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Widely separated hues. Adjacent shades of the same navy scored under
    # ffmpeg's scene threshold and the cuts went undetected, which made the
    # fixtures quietly lie about pacing.
    colors = ["0x101010", "0xe63946", "0x2a9d8f", "0xf4a261", "0x264653",
              "0xffd166", "0x6a4c93", "0x06d6a0", "0xef476f", "0x118ab2"]
    font = _font()

    chains, labels = [], []
    for i, line in enumerate(script):
        safe = line.replace(":", "").replace("'", "")
        chains.append(
            f"color=c={colors[i % len(colors)]}:s=540x960:d={shot_sec}:r=30,"
            f"drawtext=fontfile={font}:text='{safe}':fontsize={FONT_SIZE}:fontcolor=white:"
            f"x=(w-tw)/2:y=(h-th)/2:box=1:boxcolor=black@0.35:boxborderw=24[v{i}]"
        )
        labels.append(f"[v{i}]")

    graph = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(script)}:v=1:a=0[out]"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-filter_complex", graph, "-map", "[out]",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def _post(username: str, i: int, base: int, viral: bool, video: Path,
          rng: random.Random) -> dict:
    """One synthetic post. `base` is the account's typical engagement, so a
    viral post can be a clean multiple of its own median and outlier detection
    has something real to find."""
    mult = rng.uniform(3.0, 6.0) if viral else rng.uniform(0.7, 1.3)
    total = int(base * mult)
    # Full username in the code: truncating it collided every account onto the
    # same short code, which silently deduped six accounts into one.
    code = f"{username.replace('.', '')}{i:02d}"
    return {
        "id": code,
        "shortCode": code,
        "type": "Video",
        "url": f"https://www.instagram.com/p/{code}/",
        "caption": rng.choice(CAPTIONS),
        "likesCount": int(total * 0.93),
        "commentsCount": int(total * 0.07),
        "videoPlayCount": total * 14,
        # Relative to the repo root: an absolute path here would only
        # resolve on the machine that generated the fixtures.
        "videoUrl": str(video.relative_to(VIDEO_DIR.parent.parent)),
        "videoDuration": rng.uniform(12.0, 45.0),
        "ownerUsername": username,
        "timestamp": f"2026-08-{(i % 28) + 1:02d}T12:00:00.000Z",
    }


def _profile(username: str, followers: int, er: float, shot_sec: float,
             rng: random.Random) -> dict:
    base = max(1, int(followers * er))
    slug = username.replace(".", "-")
    # Only the posts that outlier detection can reach get a real video file:
    # rendering 12 per account would cost a minute of ffmpeg for nothing.
    videos = [
        _make_video(VIDEO_DIR / f"{slug}-{n}.mp4", SCRIPTS[n % len(SCRIPTS)], shot_sec)
        for n in range(2)
    ]
    # One viral post per account, at a fixed index, so runs are reproducible.
    posts = [
        _post(username, i, base, viral=(i == 3), video=videos[i % len(videos)], rng=rng)
        for i in range(12)
    ]
    return {
        "username": username,
        "fullName": username.replace(".", " ").title(),
        "biography": "Fixture account for offline runs.",
        "followersCount": followers,
        "followsCount": rng.randint(200, 2000),
        "postsCount": rng.randint(80, 900),
        "verified": False,
        "relatedProfiles": [{"username": u} for u, *_ in FIXTURE_ACCOUNTS[1:]],
        "latestPosts": posts,
    }


def build_fixtures() -> None:
    """Write the offline dataset.

    Deliberately includes one account far outside the seed's follower band
    (sample.toobig) so the peer filter has something to reject, and one flat
    account so outlier detection has somewhere to correctly find nothing.
    """
    rng = random.Random(7)
    print(f"Rendering sample reels into {VIDEO_DIR} (first run only)")
    profiles = {u: _profile(u, f, er, shot, rng) for u, f, er, shot in FIXTURE_ACCOUNTS}

    seed = FIXTURE_ACCOUNTS[0][0]
    sources.save_fixture(f"profiles_{seed}", [profiles[seed]])
    sources.save_fixture("profiles_batch", [profiles[u] for u, *_ in FIXTURE_ACCOUNTS[1:]])
    for username, profile in profiles.items():
        sources.save_fixture(f"posts_{username}", profile["latestPosts"])

    print(f"Wrote fixtures for {len(profiles)} accounts to {sources.FIXTURES}")
    print(f"Now run:  python -m outlier.cli run {seed}")


def main(argv: list[str]) -> int:
    _load_env()
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0

    cmd, *rest = argv

    if cmd == "fixtures":
        build_fixtures()
        return 0

    if cmd == "serve":
        import uvicorn

        uvicorn.run("app:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
        return 0

    if cmd == "watch":
        action, *args = rest or ["list"]
        if action == "add":
            if not args:
                print("usage: python -m outlier.cli watch add <handle> [category]")
                return 2
            handle, category = args[0], (args[1] if len(args) > 1 else "competitor")
            store.watch_add(handle, category)
            print(f"Watching @{handle.lstrip('@')} under '{category}'")
            return 0
        if action in {"rm", "remove"}:
            if not args:
                print("usage: python -m outlier.cli watch rm <handle> [category]")
                return 2
            n = store.watch_remove(args[0], args[1] if len(args) > 1 else None)
            print(f"Removed {n} entr{'y' if n == 1 else 'ies'}")
            return 0
        for row in store.watchlist():
            print(f"  [{row['category']:<12}] @{row['username']}")
        return 0

    if cmd == "track":
        digest = track.track(rest[0] if rest else None)
        text = track.digest_markdown(digest)
        out = Path("data") / "digest.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print("\n" + text)
        print(f"Saved to {out}")
        return 0

    if cmd == "run":
        if not rest:
            print("usage: python -m outlier.cli run <handle>")
            return 2
        if not sources.has_token():
            print("No APIFY_TOKEN set, running against fixtures.\n")
        manual: list[str] = []
        if "--peers" in rest:
            i = rest.index("--peers")
            manual = [h.strip().lstrip("@") for h in rest[i + 1].split(",") if h.strip()]
            rest = rest[:i]
        report = pipeline.run(rest[0], manual_peers=manual)
        out = Path("data") / f"report-{report['seed']}-{report['id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        verdict = report.get("verdict")
        if not verdict:
            verdict = ("(no verdict: no competitor teardowns survived)"
                       if not report.get("competitors")
                       else "(no verdict: no LLM key set)")
        print(f"\n{verdict}\n")
        for gap in report.get("gaps", []):
            print(f"  [{gap.get('dimension')}] {gap.get('fix')}")
        print(f"\nFull report: {out}")
        print(f"Dashboard:   http://127.0.0.1:8000/run/{report['id']}")
        return 0

    print(f"Unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
