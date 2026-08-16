"""Dashboard. Server-rendered HTML, no build step, no frontend framework.

    python -m outlier.cli serve      then open http://127.0.0.1:8000

Runs and tracking passes are kicked off as background tasks because both take
minutes; the page polls a small status endpoint rather than holding a request
open. Job state lives in memory on purpose: it is progress reporting for a
single-operator tool, and anything that matters is already in SQLite.
"""

from __future__ import annotations

import html
import threading
import traceback

from fastapi import BackgroundTasks, FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from outlier import pipeline, store, track

app = FastAPI(title="Outlier")

pipeline.FRAMES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/frames", StaticFiles(directory=pipeline.FRAMES_DIR), name="frames")

JOBS: dict[str, dict] = {}
_lock = threading.Lock()


def _set_job(name: str, **fields) -> None:
    with _lock:
        JOBS[name] = {**JOBS.get(name, {}), **fields}


# ---------------------------------------------------------------- styling

# Design tokens lifted from the Lovable design (project "Insight Reels"), read
# straight off its computed styles so the two surfaces stay identical: Space
# Grotesk for display, IBM Plex Mono for anything numeric or machine-ish, an
# oklch palette with a lime primary, amber for lift and blue for signal.
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
:root {
  --bg: oklch(16% .012 260);
  --fg: oklch(96% .005 250);
  --surface: oklch(20% .014 260);
  --surface-2: oklch(24.5% .016 260);
  --grid: oklch(30% .016 260);
  --line: oklch(30% .016 260);
  --muted: oklch(68% .018 258);
  --primary: oklch(88% .19 118);
  --primary-fg: oklch(19% .05 130);
  --lift: oklch(82% .16 72);
  --signal: oklch(72% .15 215);
  --radius: .5rem;
  --display: "Space Grotesk", ui-sans-serif, system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.6 var(--display);
  /* The faint grid is the design's signature. It reads as instrumentation,
     which is what this tool is. */
  background-image:linear-gradient(var(--grid) 1px, transparent 1px),
                   linear-gradient(90deg, var(--grid) 1px, transparent 1px);
  background-size:48px 48px;
  background-position:-1px -1px;
}
body::before { content:""; position:fixed; inset:0; background:var(--bg);
               opacity:.86; z-index:-1; }
a { color:var(--primary); text-decoration:none; }
a:hover { text-decoration:underline; }
.wrap { max-width:1080px; margin:0 auto; padding:0 20px 80px; }
.topbar { display:flex; align-items:center; gap:16px; padding:18px 0 26px;
          border-bottom:1px solid var(--line); margin-bottom:28px; }
.brand { font-family:var(--mono); font-weight:600; letter-spacing:.22em;
         text-transform:uppercase; font-size:14px; }
.brand b { color:var(--primary); }
.topbar .note { margin-left:auto; font-family:var(--mono); font-size:12px;
                color:var(--muted); }
h1 { font-size:36px; font-weight:600; letter-spacing:-.9px; margin:0 0 10px;
     max-width:20ch; line-height:1.15; }
h2 { font-family:var(--mono); font-size:12px; text-transform:uppercase;
     letter-spacing:.18em; color:var(--muted); margin:38px 0 12px; font-weight:500; }
h3 { font-size:17px; margin:0 0 6px; font-weight:600; }
.sub { color:var(--muted); margin:0 0 26px; max-width:62ch; }
.card { background:var(--surface); border:1px solid var(--line);
        border-radius:var(--radius); padding:16px 18px; margin-bottom:12px; }
.row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; }
.pill { display:inline-block; padding:2px 10px; border-radius:99px; font-size:12px;
        font-family:var(--mono); border:1px solid var(--line); color:var(--muted); }
.pill.hot { border-color:var(--lift); color:var(--lift); }
.stat, .dim.stat { font-family:var(--mono); font-variant-numeric:tabular-nums; }
.dim { color:var(--muted); }
.frames { display:flex; gap:6px; overflow-x:auto; margin:12px 0; padding-bottom:4px; }
.frames img { height:170px; border-radius:6px; border:1px solid var(--line); }
input, select, button { font:inherit; font-family:var(--mono); font-size:14px;
        padding:9px 12px; border-radius:var(--radius);
        border:1px solid var(--line); background:var(--surface-2); color:var(--fg); }
input::placeholder { color:var(--muted); }
button { background:var(--primary); border-color:var(--primary);
         color:var(--primary-fg); cursor:pointer; font-weight:600; }
button:hover { filter:brightness(1.08); }
button.ghost { background:transparent; color:var(--primary);
               border:1px solid var(--primary); width:100%; }
table { width:100%; border-collapse:collapse; }
td, th { text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); }
td { font-size:14px; }
th { font-family:var(--mono); color:var(--muted); font-weight:500; font-size:11px;
     text-transform:uppercase; letter-spacing:.12em; }
.quote { border-left:2px solid var(--primary); padding-left:14px; margin:10px 0;
         font-size:17px; line-height:1.4; }
.empty { color:var(--muted); padding:22px; text-align:center; font-family:var(--mono);
         font-size:13px; }
.banner { background:var(--surface-2); border:1px solid var(--signal);
          border-radius:var(--radius); padding:11px 15px; margin-bottom:18px;
          font-family:var(--mono); font-size:13px; }
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body><div class=wrap>"
        f"<div class=topbar><span class=brand><b>&#9678;</b> Outlier</span>"
        f"<a href='/' class='pill'>radar</a>"
        f"<span class=note>public data only &middot; no login</span></div>"
        f"{body}</div>"
        f"<script>"
        f"async function poll(){{const r=await fetch('/api/jobs');const j=await r.json();"
        f"const busy=Object.values(j).some(v=>v.state==='running');"
        f"if(busy)setTimeout(poll,3000);else if(document.getElementById('busy'))"
        f"location.reload();}}"
        f"if(document.getElementById('busy'))poll();"
        f"</script></body></html>"
    )


def e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _busy_banner() -> str:
    running = [f"{k}: {v.get('step', 'working')}" for k, v in JOBS.items()
               if v.get("state") == "running"]
    if not running:
        failed = [f"{k} failed: {v.get('error')}" for k, v in JOBS.items()
                  if v.get("state") == "error"]
        return f"<div class=banner>{e(failed[0])}</div>" if failed else ""
    return f"<div class=banner id=busy>Running &mdash; {e(running[0])}. This page refreshes itself.</div>"


# ---------------------------------------------------------------- views


@app.get("/", response_class=HTMLResponse)
def home():
    cats = store.categories()
    watched = store.watchlist()
    breakouts = store.recent_breakouts(limit=12)
    runs = store.list_runs(limit=10)

    by_cat: dict[str, list[dict]] = {}
    for w in watched:
        by_cat.setdefault(w["category"], []).append(w)

    cat_html = ""
    for category, rows in sorted(by_cat.items()):
        items = ""
        for r in rows:
            t = store.trend(r["username"])
            snaps = store.snapshots(r["username"], limit=1)
            followers = f"{snaps[0]['followers']:,}" if snaps else "not yet captured"
            delta = ""
            if t:
                cls = "hot" if t["followersPct"] > 0 else ""
                delta = f"<span class='pill {cls}'>{t['followersPct']:+.2f}%</span>"
            items += (
                f"<tr><td><b>@{e(r['username'])}</b></td>"
                f"<td class='stat dim'>{followers}</td><td>{delta}</td></tr>"
            )
        cat_html += (
            f"<div class=card><h3>{e(category)} <span class=dim>({len(rows)})</span></h3>"
            f"<table>{items}</table></div>"
        )
    if not cat_html:
        cat_html = "<div class='card empty'>Nothing tracked yet. Add an account above.</div>"

    breakout_html = "".join(
        _teardown_card({**b, "category": b.get("category")}, b["username"])
        for b in breakouts
    ) or "<div class='card empty'>No breakouts detected yet. Run a tracking pass.</div>"

    run_html = "".join(
        f"<tr><td><a href='/run/{r['id']}'>@{e(r['seed'])}</a></td>"
        f"<td class=dim>{e(r['createdAt'][:16].replace('T', ' '))}</td></tr>"
        for r in runs
    ) or "<tr><td class=dim colspan=2>No teardowns yet.</td></tr>"

    return page("Outlier", f"""
      <h1>Outlier</h1>
      <p class=sub>Find who is beating you on Instagram, watch what they do, and get told
      exactly what to change.</p>
      {_busy_banner()}

      <div class=card>
        <form class=row action=/api/run method=post>
          <input name=handle placeholder="instagram handle" required>
          <button type=submit>Tear down vs niche</button>
        </form>
      </div>

      <div class=card>
        <form class=row action=/api/watch method=post>
          <input name=username placeholder="handle to track" required>
          <select name=category>
            <option value=learning>learning</option>
            <option value=competitor selected>competitor</option>
            <option value=aspiration>aspiration</option>
          </select>
          <button type=submit>Watch</button>
        </form>
        <form action=/api/track method=post style="margin-top:10px">
          <button type=submit>Run tracking pass now</button>
          <span class=dim>{len(watched)} accounts across {len(cats)} categories</span>
        </form>
      </div>

      <h2>Watchlist</h2>
      <div class=grid>{cat_html}</div>

      <h2>New breakouts</h2>
      {breakout_html}

      <h2>Teardowns</h2>
      <div class=card><table><tr><th>Account</th><th>When</th></tr>{run_html}</table></div>
    """)


def _frames_html(frames) -> str:
    if not frames:
        return ""
    imgs = "".join(f"<img src='/frames/{e(f)}' loading=lazy alt=keyframe>" for f in frames)
    return f"<div class=frames>{imgs}</div>"


def _teardown_card(t: dict, who: str) -> str:
    r = t.get("rhythm") or {}
    bits = [f"<div class=row><h3>@{e(who)}</h3>"]
    if t.get("lift"):
        bits.append(f"<span class='pill hot'>{e(t['lift'])}x median</span>")
    if t.get("category"):
        bits.append(f"<span class=pill>{e(t['category'])}</span>")
    if t.get("hookTechnique"):
        bits.append(f"<span class=pill>{e(t['hookTechnique'])}</span>")
    if t.get("format"):
        bits.append(f"<span class=pill>{e(t['format'])}</span>")
    bits.append("</div>")
    if t.get("hookLine"):
        bits.append(f"<div class=quote>{e(t['hookLine'])}</div>")
    bits.append(
        f"<div class='dim stat'>{e(r.get('cuts'))} cuts in {e(r.get('durationSec'))}s "
        f"&middot; avg shot {e(r.get('avgShotSec'))}s &middot; first cut "
        f"{e(r.get('firstCutSec'))}s &middot; {e(t.get('likes'))} likes</div>"
    )
    bits.append(_frames_html(t.get("frames")))
    if t.get("whyItWorked"):
        bits.append(f"<p>{e(t['whyItWorked'])}</p>")
    if t.get("replicable"):
        bits.append(f"<p><b>Steal this:</b> {e(t['replicable'])}</p>")
    if t.get("url"):
        bits.append(f"<p><a href='{e(t['url'])}' target=_blank rel=noopener>view on Instagram</a></p>")
    return f"<div class=card>{''.join(bits)}</div>"


@app.get("/run/{run_id}", response_class=HTMLResponse)
def run_view(run_id: int):
    report = store.get_run(run_id)
    if not report:
        return page("Not found", "<h1>No such run</h1><p><a href=/>back</a></p>")

    you = report.get("you") or {}
    gaps = "".join(
        f"<div class=card><h3>{e(g.get('dimension'))}</h3>"
        f"<p><b>Them:</b> {e(g.get('them'))}</p>"
        f"<p><b>You:</b> {e(g.get('you'))}</p>"
        f"<p><b>Fix:</b> {e(g.get('fix'))}</p></div>"
        for g in report.get("gaps", [])
    ) or "<div class='card empty'>No gap analysis (LLM key missing on this run).</div>"

    def _concept(c: dict) -> str:
        modeled = f"<p class=dim>modeled on {e(c.get('modeledOn'))}</p>" if c.get("modeledOn") else ""
        return (
            f"<div class=card><h3>{e(c.get('title'))}</h3>"
            f"<div class=quote>{e(c.get('hook'))}</div>"
            f"<div class=dim>{e(c.get('format'))}</div>"
            f"<p>{e(c.get('why'))}</p>{modeled}</div>"
        )

    concepts = "".join(_concept(c) for c in report.get("concepts", []))

    peers_html = ""
    for c in report.get("competitors", []):
        peers_html += (
            f"<div class=card><div class=row><h3>@{e(c.get('username'))}</h3>"
            f"<span class=pill>{c.get('followers', 0):,} followers</span>"
            f"<span class='pill hot'>ER {(c.get('engagementRate') or 0) * 100:.2f}%</span>"
            f"</div></div>"
        )
        for t in c.get("teardowns", []):
            peers_html += _teardown_card(t, c.get("username"))

    own = "".join(_teardown_card(t, you.get("username")) for t in you.get("teardowns", []))
    verdict = (
        f"<div class=card><h3>Verdict</h3><p>{e(report['verdict'])}</p></div>"
        if report.get("verdict") else ""
    )
    when = e(report.get("createdAt", "")[:16].replace("T", " "))
    concepts_section = f"<h2>Shoot these next</h2>{concepts}" if concepts else ""

    return page(f"@{report['seed']}", f"""
      <p><a href=/>&larr; all runs</a></p>
      <h1>@{e(report['seed'])}</h1>
      <p class=sub>{you.get('followers', 0):,} followers &middot;
         ER {(you.get('engagementRate') or 0) * 100:.2f}% &middot; {when}</p>
      {verdict}
      <h2>Gaps</h2>{gaps}
      {concepts_section}
      <h2>Your best posts</h2>{own or "<div class='card empty'>No teardowns.</div>"}
      <h2>What is beating you</h2>{peers_html or "<div class='card empty'>No peer teardowns.</div>"}
    """)


# ---------------------------------------------------------------- actions


def _job(name: str, fn) -> None:
    _set_job(name, state="running", step="starting")
    try:
        fn()
        _set_job(name, state="done", step="finished")
    except Exception as exc:  # noqa: BLE001 - surfaced in the banner, not swallowed
        traceback.print_exc()
        _set_job(name, state="error", error=str(exc))


@app.post("/api/run")
def api_run(background: BackgroundTasks, handle: str = Form("")):
    handle = handle.strip().lstrip("@")
    if not handle:
        return RedirectResponse("/", status_code=303)
    background.add_task(_job, f"teardown @{handle}", lambda: pipeline.run(handle))
    return RedirectResponse("/", status_code=303)


@app.post("/api/watch")
def api_watch(username: str = Form(""), category: str = Form("competitor")):
    if username.strip():
        store.watch_add(username.strip(), category.strip() or "competitor")
    return RedirectResponse("/", status_code=303)


@app.post("/api/track")
def api_track(background: BackgroundTasks):
    background.add_task(_job, "tracking pass", lambda: track.track())
    return RedirectResponse("/", status_code=303)


@app.get("/api/jobs")
def api_jobs():
    return JSONResponse(JOBS)


@app.get("/api/runs")
def api_runs():
    return JSONResponse(store.list_runs())


@app.get("/api/run/{run_id}")
def api_run_json(run_id: int):
    report = store.get_run(run_id)
    return JSONResponse(report or {"error": "not found"}, status_code=200 if report else 404)


@app.get("/api/breakouts")
def api_breakouts(category: str | None = None):
    return JSONResponse(store.recent_breakouts(category=category))


@app.get("/api/watchlist")
def api_watchlist():
    return JSONResponse(store.watchlist())
