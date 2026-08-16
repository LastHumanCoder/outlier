"""The model layer: one multimodal teardown per reel, one synthesis per run.

The model must be vision-capable. The whole premise of this tool is that
something looks at the frames rather than guessing from the caption.

Provider is chosen by a `provider:model` string in OUTLIER_MODEL, the same
convention Pulse uses, so one env var switches vendor:

    gemini:gemini-3.7-flash                      GEMINI_API_KEY
    anthropic:claude-sonnet-4-6                  ANTHROPIC_API_KEY
    openai:gpt-4o                                OPENAI_API_KEY
    openrouter:anthropic/claude-sonnet-4.6       OPENROUTER_API_KEY

Prompts are built as provider-neutral blocks and serialized per vendor, because
all three API families disagree on how an image is attached.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

import requests

DEFAULT_MODEL = "gemini:gemini-3.7-flash"

# Headroom added to every Gemini request on top of the caller's answer budget,
# to cover thinking tokens. See the note in _chat.
THINKING_ALLOWANCE = 2048

ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
}
ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


# Transient upstream failures are routine: a live run against @hubspot died at
# the final synthesis step with a 503 "high demand" after every expensive scrape
# and teardown had already succeeded. Retrying here is far cheaper than losing
# the run.
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def _post(url: str, *, attempts: int = 4, **kw) -> requests.Response:
    last = None
    for attempt in range(attempts):
        res = requests.post(url, **kw)
        if res.status_code not in RETRY_STATUS:
            return res
        last = res
        if attempt < attempts - 1:
            time.sleep(2 ** attempt * 3)
    return last


def _model_spec() -> tuple[str, str]:
    spec = os.environ.get("OUTLIER_MODEL", DEFAULT_MODEL)
    provider, _, model = spec.partition(":")
    if provider not in ENDPOINTS:
        # A bare model id means Gemini, the default provider.
        return "gemini", spec
    return provider, model


def has_key() -> bool:
    provider, _ = _model_spec()
    return bool(os.environ.get(ENV_KEYS[provider]))


# Provider-neutral prompt blocks: ("text", str) or ("image", Path).
Block = tuple[str, object]


def _b64(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def _chat(blocks: list[Block], *, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    provider, model = _model_spec()
    key = os.environ.get(ENV_KEYS[provider])
    if not key:
        raise RuntimeError(f"{ENV_KEYS[provider]} not set (OUTLIER_MODEL={provider}:{model})")

    if provider == "gemini":
        parts = [
            {"text": v} if kind == "text" else
            {"inline_data": {"mime_type": "image/jpeg", "data": _b64(v)}}
            for kind, v in blocks
        ]
        # Gemini counts thinking tokens against maxOutputTokens. A budget sized
        # for the answer alone gets spent on thoughts and returns an empty or
        # truncated string, which looks exactly like a broken API key. Measured:
        # a trivial 3-hashtag prompt burned 275 tokens thinking before writing
        # anything, so the answer size gets a thinking allowance added to it.
        res = _post(
            f"{ENDPOINTS[provider]}/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={"contents": [{"parts": parts}],
                  "generationConfig": {"temperature": temperature,
                                       "maxOutputTokens": max_tokens + THINKING_ALLOWANCE}},
            timeout=180,
        )
        if not res.ok:
            raise RuntimeError(f"{provider} {res.status_code}: {res.text[:400]}")
        body = res.json()
        candidates = body.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"gemini returned no candidates: {res.text[:300]}")
        text = "".join(
            p.get("text", "") for p in candidates[0].get("content", {}).get("parts", [])
        )
        if not text:
            raise RuntimeError(
                f"gemini returned no text (finishReason="
                f"{candidates[0].get('finishReason')}, thoughts="
                f"{body.get('usageMetadata', {}).get('thoughtsTokenCount')})"
            )
        return text

    if provider == "anthropic":
        content = [
            {"type": "text", "text": v} if kind == "text" else
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": _b64(v)}}
            for kind, v in blocks
        ]
        res = _post(
            ENDPOINTS[provider],
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "temperature": temperature,
                  "messages": [{"role": "user", "content": content}]},
            timeout=180,
        )
        if not res.ok:
            raise RuntimeError(f"{provider} {res.status_code}: {res.text[:400]}")
        return "".join(b.get("text", "") for b in res.json().get("content", []))

    content = [
        {"type": "text", "text": v} if kind == "text" else
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(v)}"}}
        for kind, v in blocks
    ]
    res = _post(
        ENDPOINTS[provider],
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": content}],
              "temperature": temperature, "max_tokens": max_tokens},
        timeout=180,
    )
    if not res.ok:
        raise RuntimeError(f"{provider} {res.status_code}: {res.text[:400]}")
    return res.json()["choices"][0]["message"]["content"]


def _json(text: str) -> dict:
    """Pull the first JSON object out of a model response.

    Models wrap JSON in prose or fences often enough that demanding clean output
    would fail a demo for no reason. A brace-match is cheaper than a retry.
    """
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


TEARDOWN_SCHEMA = """{
  "hookLine": "the literal first spoken or on-screen line, verbatim",
  "hookTechnique": "one of: question, bold-claim, negation, callout, demo, stat, story-open, pattern-interrupt",
  "hookSeconds": 1.5,
  "format": "e.g. talking-head, voiceover-broll, screen-record, skit, tutorial, listicle",
  "onScreenText": "how text is used: style, density, placement",
  "pacing": "what the cut rhythm does to the viewer",
  "cta": "the ask, or 'none'",
  "whyItWorked": "2 sentences on the specific mechanic that drove the engagement, grounded in what you SAW in the frames",
  "replicable": "the one move a competitor could copy tomorrow"
}"""


def teardown(
    *,
    frames: list[Path],
    transcript: str,
    caption: str,
    rhythm: dict,
    lift: float,
) -> dict:
    """Analyze one outlier reel from its frames, transcript and cut rhythm.

    `lift` is passed in on purpose: telling the model this post beat its own
    account's median by 3.4x focuses it on explaining the anomaly rather than
    describing the video.
    """
    blocks: list[Block] = [(
        "text",
        f"This Instagram reel outperformed its own account's median engagement by "
        f"{lift}x. Work out why.\n\n"
        f"Cut rhythm (measured, trust this over your impression): {json.dumps(rhythm)}\n\n"
        f"Caption: {caption[:600] or '(none)'}\n\n"
        f"Transcript: {transcript[:3000] or '(no speech, this is a visual or music-led reel)'}\n\n"
        f"Below are keyframes in order, one per cut. Read the on-screen text in them.\n\n"
        f"Return strict JSON only, this shape:\n{TEARDOWN_SCHEMA}",
    )]
    blocks += [("image", f) for f in frames]
    return _json(_chat(blocks))


def infer_hashtags(*, bio: str, captions: list[str], username: str) -> list[str]:
    """Guess the hashtags this account's niche actually uses.

    Last-resort peer discovery. Plenty of accounts tag nothing topical: measured
    live, @hubspot's only tags were #hubspotpartner and #sponsored, one branded
    and one a disclosure, leaving no way to find the niche from tags alone. The
    bio and captions still describe the subject, so the model reads those and
    names tags that other accounts in the same space would plausibly use.
    """
    prompt = (
        f"Instagram account @{username}.\n"
        f"Bio: {bio[:500] or '(none)'}\n"
        f"Recent captions:\n" + "\n".join(f"- {c[:200]}" for c in captions[:10]) + "\n\n"
        "Name the 3 hashtags that OTHER accounts in this same niche most likely use. "
        "Topical tags about the subject matter, not branded tags for this company, not "
        "generic tags like #reels or #viral, and not disclosure tags like #sponsored. "
        'Return strict JSON only: {"hashtags": ["tag1", "tag2", "tag3"]} with no # prefix.'
    )
    tags = _json(_chat([("text", prompt)], temperature=0.4, max_tokens=300)).get("hashtags") or []
    return [str(t).lstrip("#").lower() for t in tags if t][:3]


GAP_SCHEMA = """{
  "verdict": "2 sentences: the single biggest thing separating this account from the winners in its niche",
  "gaps": [
    {"dimension": "hooks|pacing|format|on-screen text|CTA|topics",
     "them": "what the outperformers do, with a concrete example",
     "you": "what this account does instead",
     "fix": "the specific change, stated as an instruction"}
  ],
  "concepts": [
    {"title": "post concept title",
     "hook": "the literal opening line to say or show",
     "format": "the format to shoot it in",
     "why": "which competitor evidence this is drawn from",
     "modeledOn": "the reel URL this borrows its mechanic from"}
  ]
}"""


def gap_report(*, you: dict, competitors: list[dict]) -> dict:
    """Diff one account against the teardowns of its peers' outliers, and turn
    the difference into shootable concepts.

    Every concept is required to name the reel it borrowed from. Unsourced
    advice is the failure mode of every "AI content strategist", and a citation
    is what makes the output checkable.
    """
    prompt = (
        "You are analyzing why one Instagram account underperforms its niche.\n\n"
        f"THE ACCOUNT:\n{json.dumps(you, indent=2)[:6000]}\n\n"
        f"OUTPERFORMING PEERS AND TEARDOWNS OF THEIR BREAKOUT REELS:\n"
        f"{json.dumps(competitors, indent=2)[:14000]}\n\n"
        "Compare them on hooks, pacing, format, on-screen text, CTA and topic choice. "
        "Be specific and quantitative where the data supports it, for example "
        "'they cut every 1.4s, you every 4.1s'. Never invent a number that is not above. "
        "Then propose 5 post concepts this account should shoot next, each modeled on a "
        "specific competitor reel and each naming that reel's URL.\n\n"
        f"Return strict JSON only, this shape:\n{GAP_SCHEMA}"
    )
    return _json(_chat([("text", prompt)], max_tokens=4000))
