# Outlier: Devpost submission copy

Paste each section into the matching Devpost field.

---

## Tagline

Find who is beating you on Instagram, watch what they actually do, and get told what to change.

---

## Inspiration

Almost every "AI for creators" tool writes captions. That is the easy half of the job. The hard half
is knowing what to post in the first place, and that answer is already sitting in your niche:
somebody near your size just had a post go several times their own average, and there is a specific
reason it did.

The reason is almost never the caption. It is the pacing, the first 1.5 seconds, whether the video
loops, whether there is a CTA killing the share. Nobody measures those, because measuring them means
actually downloading the video and looking at it. So we built the thing that downloads the video and
looks at it.

The second half of the inspiration was distrust. Every AI strategist tool we tried produced advice
with no source attached, which makes it impossible to tell insight from hallucination. We decided
early that every recommendation Outlier makes has to name the reel URL it was derived from, so a user
can click it and check.

## What it does

You give Outlier one Instagram handle. It returns a report telling that account why it is losing to
its niche, with the evidence attached.

The loop:

1. Scrape the seed profile and its post history.
2. Discover peers: Instagram's own related-profile graph first, then hashtag co-occurrence, then
   hashtags inferred from the bio and captions, then handles you pass manually.
3. Size-band the peers to 0.2x to 5x the seed's followers, because advice from an account 1000x
   bigger is not advice. If fewer than three candidates fit, it compares against the closest by size
   and says out loud that the comparison is looser.
4. Rank survivors by engagement rate, not follower count.
5. In each peer's history, find posts that beat that account's own median by 2x or more. This is
   what we call lift, and it isolates the creative decision from the follower count.
6. Tear down each of those reels: download it from Instagram's public CDN with no login, extract one
   keyframe per cut with ffmpeg, measure the cut rhythm, pull a transcript through VideoDB, and send
   the frames to a vision model to explain the mechanic.
7. Do the same to the seed's own best posts, so the comparison is like for like.
8. Diff the two and output a verdict, six gaps, and five concepts to shoot next, each citing the reel
   whose mechanic it borrows.

The measurement nobody else has is cut rhythm, pulled from ffmpeg's scene detector: total cuts,
average shot length, and seconds until the first cut. In the live @hubspot run it is the whole story.
HubSpot posts 72 and 88 second reels that take 4.3 to 7.2 seconds to reach a hook. The peer that is
converting attention 4.8x better posts a 17.65 second single continuous take and a 1.51 second loop,
zero cuts in both, no CTA in either. That difference is invisible to any tool reading captions.

There is also a standing mode. Add accounts to a watchlist under `learning`, `competitor` or
`aspiration`, put `track` on a cron, and every pass snapshots each account's numbers and tears down
only the breakouts it has not seen before. A daily pass over 20 accounts only pays for genuinely new
posts.

And there is a server-rendered dashboard with no build step: the radar feed, the watchlist with
follower and engagement-rate movement, the past teardowns, and the keyframe filmstrips served
straight off disk.

## How we built it

Python 3.12, no framework beyond FastAPI for the dashboard, SQLite for everything persistent.

- `outlier/metrics.py` is pure ranking math with no I/O: engagement rates, size banding, outlier
  detection. Deterministic, so the interesting logic is testable with zero API keys.
- `outlier/sources.py` calls two Apify actors (profile scraper and post scraper) plus a hashtag
  actor, through the synchronous dataset endpoint. No Instagram login anywhere in the codebase.
- `outlier/media.py` is ffmpeg and VideoDB. We deliberately chose ffmpeg alone over
  opencv plus scenedetect plus faster-whisper: one binary already on the machine, no wheels to
  compile, and the scene scores ffmpeg prints for free are exactly the cut-rhythm signal we needed.
  Keyframes are one per cut, capped at six, with the opening frame always forced in because the hook
  is the most important frame in a reel.
- `outlier/brain.py` builds provider-neutral prompt blocks and serializes them per vendor, so
  `OUTLIER_MODEL=provider:model` swaps between Gemini, Anthropic, OpenAI and OpenRouter. It must be
  vision-capable; the entire premise is that something looks at the frames.
- `outlier/store.py` memoizes every expensive step in SQLite keyed by post shortcode, so iterating on
  the report prompt does not re-scrape, re-download or re-analyze anything.
- Teardowns run four at a time in a thread pool, since the work is network-bound.

The pipeline degrades one layer at a time rather than failing. With no Apify token it runs against
fixtures, and the fixture generator renders real MP4s with ffmpeg at deliberately different cut
rhythms, so the whole media pipeline genuinely runs offline against actual video files. With no LLM
key you still get metrics, keyframes and cut rhythm, just no written analysis. With no VideoDB key
teardowns run on frames and caption only.

## Challenges we ran into

**VideoDB's upload returns an async job id, not a video id.** `/collection/default/upload` hands back
an id that looks like the thing you want. Passing it to the transcription endpoint fails with
"Invalid video id", which reads exactly like an auth problem and sent us hunting a key issue that did
not exist. The real video id only appears in the payload of `/async-response/{job_id}` once its
status flips to `complete`. Two separate waits are needed: one for the upload job, one for speech
indexing after that.

**Gemini counts thinking tokens against maxOutputTokens.** A budget sized for the answer alone gets
spent on thoughts, and the API returns an empty string with `finishReason: MAX_TOKENS`. That also
looks exactly like a broken key. We measured it: a trivial three-hashtag prompt burned 275 tokens
thinking before writing a single character. Every Gemini call now adds a fixed thinking allowance on
top of whatever budget the caller asked for.

**Instagram's `relatedProfiles` is empty for most accounts.** We built peer discovery on it, then
measured against the live API: garyvee returns 49 related profiles, hubspot returns 0, thefutur
returns 0. Relying on it alone meant the tool simply did not work for the majority of accounts. Peer
discovery became a four-step fallback chain instead, ending in manual handle entry.

**The hashtag fallback surfaces dead accounts.** Hashtag co-occurrence finds the niche, but it also
finds abandoned and spam accounts. On the live @hubspot run, four of the five selected peers had zero
engagement across their entire history, so the whole niche comparison was resting on one real
account. We now drop any peer with no engagement at all before ranking, and keep a deliberately wider
candidate pool than the report needs so the ranking step has something to discard.

**An empty result got cached permanently.** The memo cache was doing its job too well. A lookup that
returned an empty list because the scrape failed got written to SQLite as if it were the answer, and
every retry after that was served the same empty list forever. Now failures and empty results are
both left uncached. Every producer in the pipeline returns a list of scraped items, and an empty one
always means the lookup did not work rather than that the answer is genuinely nothing.

**Transient 503s at the worst moment.** One live run died at the final synthesis step with a "high
demand" 503 after every expensive scrape and teardown had already succeeded. Model calls now retry
with backoff on the retryable status codes.

## Accomplishments that we're proud of

Verified end to end against live Instagram data, @hubspot at 656,402 followers, not fixtures and not
mockups:

- Profile and post scraping, peer discovery through all three automatic paths, size banding,
  engagement-rate ranking and outlier detection.
- Reels downloaded straight off Instagram's CDN with no login, run through ffmpeg scene detection,
  keyframe extraction and cut-rhythm measurement. Eight real reels torn down, keyframes on disk.
- The vision teardown reading burned-in on-screen text off the frames, including a lower-third
  identifier and non-English meme text.
- A full gap report where every number is traceable to a measurement and every cited reel URL is
  present in the scraped data. Six gaps and five concepts, each concept naming the reel it borrows
  from.
- SQLite persistence, memo cache, watchlist, snapshots, trends, breakout deduplication, dashboard
  rendering including keyframe serving, and VideoDB upload and job polling against the live API.

The verdict on that run is the kind of thing that would take a human analyst a day: HubSpot ships
long, highly produced sponsored videos with explicit conversion CTAs, while the peer beating them on
engagement rate ships short zero-cut loops with no CTA at all. Both halves of that sentence are
backed by measurements from files we downloaded, not by an impression of the caption.

We are also proud that it runs with zero API keys. `python -m outlier.cli fixtures` synthesizes six
accounts and renders real MP4s for them, each with a different cut rhythm, so a reviewer can watch
the download, scene detection, keyframe extraction and rhythm measurement all genuinely execute
offline. It is sample data, not a mocked pipeline.

## What we learned

Relative beats absolute. "Most popular post in the niche" just finds the biggest account. A post that
beat its own account's median by 2.68x isolates the creative decision from the follower count, and
that is the only comparison that transfers to a smaller account.

Measure the thing that is hard to measure. Caption analysis is cheap, so everyone does it and it is
worth nothing. Downloading the video and counting the cuts is annoying, so nobody does it, and it
turned out to be where the actual difference lives.

Two API errors that read as authentication failures were not authentication failures. Both the
VideoDB job id and the Gemini thinking-token trap produce symptoms that point at your credentials.
We now read the response body before we read the key.

Cache correctness is about what you refuse to cache. Caching successes made the tool affordable.
Caching a failure or an empty result made it permanently broken in a way that was hard to see.

Fallback chains are not edge cases. When the primary data source is missing for the majority of
inputs, the fallback is the product, and it deserves the same care as the happy path.

## What's next

**Verify transcripts on a speech-bearing reel.** This is the honest gap. VideoDB upload and job
polling are confirmed working against the live API, but every reel we tested returned no speech,
which is normal for music-led and text-on-screen content. That means the transcript-to-analysis path
has not yet been exercised with real text. It is the first thing we will close.

**Better peer discovery for very large seeds.** @hubspot at 656k has no related profiles and only
branded and disclosure hashtags, so the closest live peer we found sits at 17k followers, well
outside the intended 0.2x to 5x band. The run says so, which is the right behaviour, but a large
account deserves a better source of comparable peers than hashtag co-occurrence.

**Crossfade-aware cut detection.** The scene threshold is a fixed 0.3, which measures hard-cut
short-form content accurately and under-counts slow crossfades.

**Digest by email.** The tracking pass already writes a markdown digest to disk that reads fine in a
mail client. Sending it is the small remaining step.

**Concept to shot list.** Right now Outlier writes a title, a hook line and a format. The natural
next layer is a shot list with per-shot durations derived from the cut rhythm it already measured on
the reel the concept is modeled on.

## Built with

Python 3.12, FastAPI, SQLite, ffmpeg, Apify actors (instagram-profile-scraper,
instagram-post-scraper, instagram-hashtag-scraper), VideoDB, Google Gemini.

Provider-agnostic model layer, so Anthropic, OpenAI and OpenRouter models work through the same env
var. No Instagram login and no session cookie anywhere: all data is public.
