# Outlier

**Find who is beating you on Instagram, watch what they actually do, and get told exactly what to change.**

Most "AI for creators" tools write your captions. That is the easy half. The hard half is knowing
*what to post in the first place*, and that answer is already sitting in your niche: somebody near
your size just had a post go 5x their own average, and there is a reason it did.

Outlier finds those posts, downloads them, looks at them frame by frame, measures how they are cut,
and turns the difference between them and you into a list of things to shoot next.

```
one handle
  -> scrape the profile, read Instagram's own related-profile graph
  -> keep only accounts in the same weight class (0.2x to 5x your followers)
  -> rank them by engagement RATE, not follower count
  -> in each one's history, find posts that beat THAT ACCOUNT'S OWN median
  -> download those reels: keyframes, cut rhythm, transcript
  -> a vision model reads the frames and explains the mechanic
  -> diff it against your own best posts
  -> output: the gaps, and 5 concepts to shoot, each citing the reel it came from
```

Plus a standing **watchlist** so this keeps running: categorize accounts as `learning`,
`competitor` or `aspiration`, and every tracking pass snapshots their numbers and tears down only
the breakouts it has not seen before.

---

## Why this is not another caption generator

**It measures the thing nobody measures.** Every teardown carries a real cut rhythm, pulled from
ffmpeg's scene detector: total cuts, average shot length, and how many seconds until the first cut.
That is usually the actual difference between a reel that holds attention and one that does not,
and no amount of caption analysis will find it. In the bundled sample data the winning account cuts
every 1.4 seconds and the losing one sits on a single shot for 10.5.

**Outliers are relative, not absolute.** "Most popular post" just finds the biggest account. A post
that beat *its own account's* median by 4x isolates the creative decision from the follower count,
which is the only comparison that transfers to you.

**Peers are size-banded.** Instagram's related-profile graph will happily hand back Nike when you
ask about a 12k account. Anything outside 0.2x to 5x your size gets dropped, because advice derived
from an account 1000x bigger is not advice.

**Every recommendation cites its source.** Each generated concept has to name the reel URL whose
mechanic it borrows. Unsourced advice is the failure mode of every AI strategist tool, and a
citation is what makes the output checkable.

**No Instagram login, anywhere.** Scraping is Apify's public-data actors, video fetching is the
public CDN URL. Nothing in this repo wants your session cookie.

---

## Install

Needs Python 3.11+ and `ffmpeg` on PATH.

```bash
git clone https://github.com/LastHumanCoder/outlier && cd outlier
uv venv --python 3.12 .venv && uv pip install -r requirements.txt
cp .env.example .env    # then fill in whichever keys you have
```

Nothing is mandatory. The pipeline degrades one layer at a time:

| Key | Without it |
|---|---|
| `APIFY_TOKEN` | Runs against `fixtures/`, a synthesized six-account niche |
| `GEMINI_API_KEY` (or Anthropic/OpenAI/OpenRouter) | You get metrics, keyframes and cut rhythm, no written analysis |
| `VIDEODB_API_KEY` | Teardowns run on frames and caption only, no transcript |

Set the model with `OUTLIER_MODEL=provider:model`. Default is `gemini:gemini-3.7-flash`; also
supported are `anthropic:claude-sonnet-4-6`, `openai:gpt-4o` and
`openrouter:anthropic/claude-sonnet-4.6`. It must be vision-capable.

## See it without installing anything

[`samples/`](samples/) holds a real teardown of `@hubspot` produced by this tool: the verdict, six
gaps, five sourced concepts, and the measured cut rhythm of every reel involved. Nothing in it is
illustrative.

## Deploy it

```bash
railway up
```

`Dockerfile` and `railway.toml` are included. The image installs ffmpeg, the three Python
dependencies, and renders the sample dataset at build time so a fresh deploy has something to show
rather than an empty dashboard. Set `APIFY_TOKEN`, `GEMINI_API_KEY` and `VIDEODB_API_KEY` as
service variables to enable live runs. It listens on `$PORT`.

## Run it with no keys at all

```bash
.venv/bin/python -m outlier.cli fixtures
.venv/bin/python -m outlier.cli run sample.seed
.venv/bin/python -m outlier.cli serve      # http://127.0.0.1:8000
```

`sample.*` handles always resolve to the bundled fixtures, so this works identically whether or not
you have API keys set.

`fixtures` synthesizes six sample accounts *and renders real MP4s for them with ffmpeg*, each with a
deliberately different cut rhythm. So the whole media pipeline (download, scene detection, keyframe
extraction, rhythm measurement) genuinely runs offline. It is sample data, not a mocked-out
pipeline: the numbers in the report were measured from actual video files.

## Real usage

```bash
cp .env.example .env && $EDITOR .env      # keys are read from here

.venv/bin/python -m outlier.cli run nike                       # teardown vs the niche
.venv/bin/python -m outlier.cli run nike --peers rival1,rival2 # or name the peers yourself

.venv/bin/python -m outlier.cli watch add somecreator learning
.venv/bin/python -m outlier.cli watch add rival competitor
.venv/bin/python -m outlier.cli track                          # writes data/digest.md
```

Put `track` on a schedule and it becomes a standing radar:

```bash
0 9 * * *  cd /path/to/outlier && .venv/bin/python -m outlier.cli track
```

---

## How it is put together

| File | Job |
|---|---|
| `outlier/metrics.py` | Engagement rates, peer banding, outlier detection. Pure, no I/O, fully tested |
| `outlier/sources.py` | Apify actors, with transparent fixture fallback |
| `outlier/media.py` | ffmpeg keyframes and cut rhythm, VideoDB transcripts |
| `outlier/brain.py` | Multimodal teardown and gap synthesis, provider-agnostic |
| `outlier/pipeline.py` | The one-shot run |
| `outlier/track.py` | Watchlist passes, snapshots, breakout detection, digest |
| `outlier/store.py` | SQLite: runs, snapshots, breakouts, and a memo cache |
| `app.py` | Server-rendered dashboard, no build step |

**Two decisions worth explaining.**

*ffmpeg instead of opencv + scenedetect + faster-whisper.* One binary that is already installed,
zero wheels to compile, and the scene scores ffmpeg prints for free are exactly the cut-rhythm
signal the analysis needs. The heavyweight stack would have bought nothing here.

*Everything expensive is memoized in SQLite.* Teardowns are keyed by post shortcode, so iterating on
the report prompt does not re-scrape, re-download or re-analyze anything. Failures are deliberately
not cached, so a run that died on a flaky network call retries cleanly. This is also what makes
tracking affordable: a daily pass over 20 accounts only pays for genuinely new breakouts.

## Tests

```bash
python test_outlier.py
```

Covers the ranking math and response parsing: the places where a silent bug would send the pipeline
off to analyze the wrong reels while still producing a plausible-looking report.

---

## Honest status

Verified end to end against live Instagram data (`@hubspot`, 656k followers):

- Profile and post scraping, peer discovery via all three paths, size banding, ER ranking,
  outlier detection (10 asserts, all passing)
- Reel download straight off Instagram's CDN with no login, ffmpeg scene detection, keyframe
  extraction, cut-rhythm measurement
- The vision teardown, reading burned-in on-screen text off the frames
- The full gap report, with every number in it traceable to a measurement and every cited reel
  URL present in the scraped data
- SQLite persistence, memo cache, watchlist, snapshots, trends, breakout dedup
- Dashboard rendering, including keyframe serving
- VideoDB upload and job polling against the live API

Not yet verified:

- **Transcripts on a speech-bearing video.** Upload and polling are confirmed. Every reel tested
  so far returned no speech, which is normal for music-led and text-on-screen content but means
  the transcript-to-analysis path has not been exercised with real text.

Two API traps worth flagging, both of which cost real debugging time:

**VideoDB.** `/collection/default/upload` returns an **async job id**, not a video id. Passing it to
the transcription endpoint fails with "Invalid video id", which reads like an auth problem and is
not. Poll `/async-response/{job_id}` until status is `complete` and take the id from the payload.

**Gemini.** `maxOutputTokens` includes thinking tokens. A budget sized for the answer alone gets
spent on thoughts and returns an **empty string with `finishReason: MAX_TOKENS`**, which looks
exactly like a broken key. Measured: a trivial three-hashtag prompt burned 275 tokens thinking
before writing a character. Every Gemini call here adds a thinking allowance on top of the
caller's answer budget.

## Limits

- Apify's profile actor caps at 12 recent posts. With a token, `fetch_posts` pulls a deeper history,
  because a median over 12 posts is one viral post away from meaningless.
- `relatedProfiles` is only populated for some accounts. Measured live: garyvee 49, hubspot 0,
  thefutur 0. Peer discovery therefore falls back to hashtag co-occurrence, then to hashtags
  inferred from the bio and captions, then to handles you pass with `--peers`.
- Hashtag discovery surfaces abandoned and spam accounts. Peers with zero engagement are dropped,
  but for a very large seed the closest available peers may still be far smaller. The run says so
  when the comparison is looser than the 0.2x-5x band.
- Cut detection uses a fixed threshold of 0.3. Hard-cut short-form content is measured accurately;
  slow crossfades will under-count.
