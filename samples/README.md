# Sample runs

Real output, committed so you can see what this tool produces without installing
anything or spending a cent on API calls.

## `report-hubspot.json`

A live teardown of [@hubspot](https://www.instagram.com/hubspot/) (656,402 followers) against
peers found in its niche. Nothing here is hand-written or illustrative: every reel was scraped,
downloaded and measured.

**The verdict it produced:**

> HubSpot's content, while attempting entertainment, remains too product-centric and lacks the
> extreme, culturally resonant, or emotionally provocative elements that drive viral engagement in
> its niche. Its consistent, deliberate pacing and direct CTAs prioritize information delivery over
> the immediate, captivating hooks and diverse formats that outperformers use.

**One of the six gaps, verbatim:**

| | |
|---|---|
| **Them** | Hooks trigger immediately within 1.5s using visual shock value or bizarre text overlays |
| **You** | Hooks take 4.29s to 7.2s to establish context, with slow narrative ramp-ups |
| **Fix** | Deliver the visual punchline or polarizing premise in the first 1.5 seconds |

Those numbers are not the model's impression of the videos. They are ffmpeg measurements taken from
the downloaded files, handed to the model as ground truth.

### What to look at in the JSON

| Path | What it is |
|---|---|
| `verdict` | The single biggest difference, in two sentences |
| `gaps[]` | Six dimensions, each as them / you / fix |
| `concepts[]` | Five post ideas, each citing the reel URL whose mechanic it borrows |
| `you.teardowns[]` | The seed account's own best reels, analyzed the same way |
| `competitors[].teardowns[]` | Each peer's breakout reels |
| `*.rhythm` | Measured cut rhythm: `cuts`, `durationSec`, `avgShotSec`, `firstCutSec` |
| `*.lift` | How many times this post beat its own account's median |

Every `concepts[].modeledOn` URL appears somewhere in the teardowns of the same report. That is
checkable, and it is the point: unsourced advice is the failure mode of every tool in this space.

## Reproducing it

```bash
export APIFY_TOKEN=... GEMINI_API_KEY=...
python -m outlier.cli run hubspot
```

Expect two to six minutes. Instagram CDN URLs expire, so a re-run downloads fresh copies of the
reels rather than reusing the ones behind this report.

To see the pipeline work with no keys and no network at all:

```bash
python -m outlier.cli fixtures
python -m outlier.cli run sample.seed
```

The fixtures are not mocks. `fixtures` renders real MP4s with ffmpeg, each with a deliberately
different cut rhythm, so scene detection and keyframe extraction genuinely run and the numbers in
the resulting report were measured rather than made up.
