# Outlier: full scope

For design. Everything below either exists in the working build or is specified precisely enough to
build. Field names match what the code actually produces, so a design can bind directly to them.

---

## 1. What it is

**Outlier tells an Instagram account why it is losing to its niche, using evidence from the reels
that are actually winning.**

Not a caption generator. The insight it sells is *what to make and why*, derived from posts that
outperformed their own account's median. The proof it shows is visual: the actual keyframes of the
competitor's reel, with the measured cut rhythm underneath.

**One sentence for the landing state:** Find who is beating you, watch what they actually do, and
get told exactly what to change.

## 2. Who it is for

A creator, social manager or founder running one account seriously. They post regularly, they know
their numbers are flat, and they do not know whether the problem is their hooks, their pacing, their
format or their topics. They have looked at competitors manually and learned nothing they could act
on.

Two jobs, and the design has to serve both:

- **Job A, diagnostic (one-shot).** "Why am I behind right now?" Runs once, produces a report.
- **Job B, radar (standing).** "What just started working in my niche this week?" Runs on a
  schedule over a watchlist, produces a feed.

Job A is the demo and the hook. Job B is why anyone keeps the tool.

## 3. The core loop

```
handle
  1  scrape profile         followers, bio, related profiles
  2  discover peers         Instagram's related graph, or hashtag co-occurrence, or manual
  3  size-band              keep 0.2x to 5x the seed's followers, loosen if that empties
  4  rank                   by engagement RATE, not follower count
  5  find outliers          posts beating their OWN account's median by 2x or more
  6  tear down each         download reel, keyframes, cut rhythm, transcript, vision model
  7  tear down your own     same treatment on your best posts, so the diff is like for like
  8  synthesize             gaps + concepts to shoot, each citing its source reel
```

Three ideas carry the whole product and should be legible in the design:

- **Lift.** A post that beat its own account's median by 4.2x. This isolates the creative decision
  from the follower count. Show it as a badge on every teardown, always with "their median" in the
  label so it is never mistaken for a growth stat.
- **Cut rhythm.** Measured from the actual video: number of cuts, average shot length, seconds until
  the first cut. This is the thing no competitor tool has and it is usually the real difference.
- **Citation.** Every recommendation names the reel it came from. Nothing in this UI should ever
  make an unsourced claim.

## 4. Screens

### 4.1 Radar (home)

The standing view. Four zones, in this priority order.

1. **Act bar.** Two inputs: a handle to tear down, and a handle to add to the watchlist with a
   category. Plus "run a tracking pass now".
2. **New breakouts.** The feed. Reverse chronological, teardown cards. This is the reason to open
   the app on a Tuesday.
3. **Watchlist.** Grouped by category, with each account's follower count and its change since the
   previous snapshot. Categories are free-form strings; the three shipped are `learning` (educators
   worth studying for technique), `competitor` (fighting you for the same follower), `aspiration`
   (several tiers up, watched for where the niche is going).
4. **Past teardowns.** A list, linking into 4.2.

Design notes: the breakout feed and the watchlist compete for the top. Breakouts should win, because
the watchlist is reference and the feed is news. An account with an empty watchlist needs a
different first screen entirely, see 4.5.

### 4.2 Teardown report

The one-shot deliverable. Reading order matters, it goes broad to specific:

1. **Header.** `@handle`, followers, engagement rate, when the run happened.
2. **Verdict.** Two sentences naming the single biggest thing separating this account from the
   winners. This is the takeaway. It should be readable in three seconds and quotable.
3. **Gaps.** Repeating unit, one per dimension (`hooks`, `pacing`, `format`, `on-screen text`,
   `CTA`, `topics`). Each gap has exactly three parts that want to be visually parallel:
   - **Them:** what the outperformers do, with a concrete example
   - **You:** what this account does instead
   - **Fix:** the change, phrased as an instruction
   The them/you pairing is the core comparison unit in the product. It deserves a real treatment,
   not three stacked paragraphs.
4. **Shoot these next.** Five concepts. Each: title, the literal opening line to say or show, the
   format to shoot it in, why (which evidence), and the reel it is modeled on. The hook line is the
   part someone will copy, so it should be the most prominent and the easiest to select.
5. **Your best posts.** Teardown cards for the seed's own top reels.
6. **What is beating you.** Per peer: a header with followers and ER, then teardown cards for their
   outliers.

### 4.3 Teardown card (the atom)

Appears in the breakout feed and in both halves of the report. Everything in it is real data:

| Element | Field | Notes |
|---|---|---|
| Handle | `username` | |
| Lift badge | `lift` | "4.19x their median". Range seen live: 2.0 to 6.5 |
| Category | `category` | Feed only |
| Technique | `hookTechnique` | One of: question, bold-claim, negation, callout, demo, stat, story-open, pattern-interrupt |
| Format | `format` | talking-head, voiceover-broll, screen-record, skit, tutorial, listicle |
| Hook line | `hookLine` | Verbatim first line. The quote. Design this as the hero of the card |
| Metrics strip | `rhythm` | cuts, durationSec, avgShotSec, firstCutSec, plus likes |
| Filmstrip | `frames` | 1 to 6 keyframes, one per cut, in order, 9:16 |
| Why it worked | `whyItWorked` | 2 sentences |
| Steal this | `replicable` | One move, copyable tomorrow |
| Source | `url` | Link out to Instagram |

**The filmstrip is the money shot.** It is the visible proof that something actually watched the
video. It is also where the burned-in on-screen text is legible, which is often the real hook. Give
it room; a cramped thumbnail row wastes the strongest asset in the product.

Real ranges to design against: duration 8 to 63 seconds, cuts 0 to 8, average shot 1.4s to 15.7s.
A card must survive `cuts = 0` (single continuous shot) and a missing `hookLine`.

### 4.4 Digest

The tracking pass output, currently markdown on disk, destined for email. Two sections: new
breakouts (heading, hook, pacing, steal-this, link) and movement (one line per account: followers,
ER, percentage change). It needs to survive being read on a phone in a mail client, so it is the one
surface that should stay plain.

### 4.5 States

Design owes real treatments to all of these, because most of them appeared in live testing:

- **Empty.** No watchlist, no runs. Needs a genuine first-run screen, not an empty table.
- **Running.** A pass takes 2 to 5 minutes. Currently a self-refreshing banner. It should say which
  account it is on and how many reels are left.
- **No LLM key.** Metrics, rhythm and keyframes exist; verdict, gaps and concepts do not. The report
  must still be worth opening. This is a real, supported mode, not an error.
- **Loose comparison.** When fewer than three candidates fall inside the 0.2x-5x band, the run
  compares against the closest by size instead and must say so. Trust depends on this being visible.
- **No transcript.** Common. Music-led reels have no speech. The card falls back to on-screen text
  from the frames and should not look broken.
- **Partial failure.** One dead CDN link costs that reel, not the run. Say what was skipped.
- **Nothing to compare against.** Private account, or no related profiles and no usable hashtags.
  The only exit is asking the user for competitor handles directly.

## 5. Data model

```
runs        id, seed, created_at, report(json)
watchlist   username, category, note, added_at        PK (username, category)
snapshots   username, captured_at, followers, median_eng, eng_rate, post_count
breakouts   short_code, username, category, detected_at, lift, teardown(json)
cache       key, value, created_at
```

An account can sit in more than one category, so any per-account UI must handle multiple category
badges. Snapshots accumulate, so a follower/ER sparkline is available for free whenever design wants
one; there is no trend at all on first capture, and the UI must say "first capture" rather than 0%.

## 6. Vocabulary

Use these words consistently, they are the product's mental model:

- **Lift**, not "virality" or "score". Always paired with "their median".
- **Breakout**, not "top post". A breakout is relative to its own account.
- **Peer**, not "competitor", in the size-banding sense. `competitor` is a watchlist category and
  means something narrower.
- **Teardown**, not "analysis".
- **Cut rhythm**, not "editing speed".

## 7. Non-goals

Deliberately not in scope, and design should not imply them:

- No posting, scheduling or publishing. Outlier decides what to make; it does not make or send it.
- No Instagram login. Everything is public data. Nothing should ask for a password or a session.
- No follower growth predictions. The tool explains what happened, it does not forecast.
- No content generation beyond the five concepts. It writes a hook line and a format, not a script.

## 8. Current constraints worth designing around

- A tracking pass over 20 accounts takes minutes, not seconds. Async is not optional.
- Apify's profile actor returns at most 12 recent posts; the post actor is used for real history.
- `relatedProfiles` is only populated for some accounts. Measured live: garyvee 49, hubspot 0,
  thefutur 0. Hashtag fallback and manual peer entry are therefore first-class paths, not edge
  cases, and the UI needs a real place to type in competitor handles.
