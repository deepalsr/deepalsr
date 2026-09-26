#!/usr/bin/env python3
"""
Self-hosted replacement for github-readme-stats / github-readme-activity-graph.

Fetches public GitHub data for one user via the GraphQL API and renders
three static SVG cards (stats, top languages, activity graph) styled to
match the deepalsr README color scheme. No third-party rendering server
involved — everything runs inside your own GitHub Actions job.

Env vars required:
  GH_TOKEN     - a token with read access to public GitHub data
                 (a classic PAT with `read:user` scope is recommended;
                 the default GITHUB_TOKEN also works for public data)
  GH_USERNAME  - the GitHub username to report on
"""

import datetime
import html
import os
import sys
from collections import defaultdict

import requests

GH_TOKEN = os.environ.get("GH_TOKEN")
GH_USERNAME = os.environ.get("GH_USERNAME")
OUT_DIR = os.environ.get("OUT_DIR", "assets/stats")

if not GH_TOKEN or not GH_USERNAME:
    print("GH_TOKEN and GH_USERNAME environment variables are required.", file=sys.stderr)
    sys.exit(1)

# ---- palette (matches the README's badge colors) ----------------------
BG = "#121318"
CARD_BG = "#1a1b21"
BORDER = "#2a2c35"
TITLE = "#C0C1FF"
ICON = "#4CD7F6"
TEXT = "#E3E1E9"
MUTED = "#8083FF"
FONT = "'JetBrains Mono', 'Segoe UI', Helvetica, Arial, sans-serif"

GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node { name color }
          }
        }
      }
    }
  }
}
"""


def fetch_data(login: str) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": QUERY, "variables": {"login": login}},
        headers={"Authorization": f"Bearer {GH_TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]["user"]


def compute_streaks(calendar: dict):
    days = []
    for week in calendar["weeks"]:
        for d in week["contributionDays"]:
            date = datetime.date.fromisoformat(d["date"])
            days.append((date, d["contributionCount"]))
    days.sort(key=lambda x: x[0])

    # longest streak anywhere in the calendar
    longest = 0
    longest_end = None
    run = 0
    run_end = None
    for date, count in days:
        if count > 0:
            run += 1
            run_end = date
            if run > longest:
                longest = run
                longest_end = run_end
        else:
            run = 0

    # current streak: walk backward from the last day; if today has no
    # contributions yet, that alone shouldn't break an ongoing streak
    idx = len(days) - 1
    if days and days[idx][1] == 0:
        idx -= 1
    current = 0
    current_start = None
    while idx >= 0 and days[idx][1] > 0:
        current += 1
        current_start = days[idx][0]
        idx -= 1

    return current, longest


def top_languages(repo_nodes):
    totals = defaultdict(int)
    colors = {}
    for repo in repo_nodes:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            totals[name] += edge["size"]
            colors[name] = edge["node"]["color"] or MUTED
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:6]
    grand_total = sum(totals.values()) or 1
    return [(name, size / grand_total, colors[name]) for name, size in ranked]


def card_shell(width, height, title, body):
    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
     xmlns="http://www.w3.org/2000/svg" font-family="{FONT}">
  <rect x="0.5" y="0.5" rx="14" width="{width - 1}" height="{height - 1}"
        fill="{CARD_BG}" stroke="{BORDER}"/>
  <text x="25" y="35" fill="{TITLE}" font-size="17" font-weight="600">{html.escape(title)}</text>
  {body}
</svg>"""


def render_stats_card(user):
    cc = user["contributionsCollection"]
    total_stars = sum(r["stargazerCount"] for r in user["repositories"]["nodes"])
    rows = [
        ("Total Stars", f"{total_stars:,}"),
        ("Total Commits", f"{cc['totalCommitContributions']:,}"),
        ("Total PRs", f"{user['pullRequests']['totalCount']:,}"),
        ("Total Issues", f"{user['issues']['totalCount']:,}"),
        ("Contributions (this yr)", f"{cc['contributionCalendar']['totalContributions']:,}"),
        ("Followers", f"{user['followers']['totalCount']:,}"),
    ]
    body_lines = []
    y = 70
    for label, value in rows:
        body_lines.append(
            f'<circle cx="30" cy="{y - 5}" r="5" fill="{ICON}"/>'
            f'<text x="45" y="{y}" fill="{TEXT}" font-size="13">{html.escape(label)}</text>'
            f'<text x="{440 - len(value) * 8}" y="{y}" fill="{TEXT}" font-size="13" font-weight="600">{html.escape(value)}</text>'
        )
        y += 32
    return card_shell(465, y + 10, f"{user['name'] or user['login']}'s GitHub Stats", "\n  ".join(body_lines))


def render_top_langs_card(user):
    langs = top_languages(user["repositories"]["nodes"])
    if not langs:
        langs = [("No data", 1.0, MUTED)]
    bar_width = 410
    body_lines = []
    y = 60
    x = 25
    # single stacked bar
    seg_x = x
    for name, frac, color in langs:
        seg_w = max(bar_width * frac, 2)
        body_lines.append(f'<rect x="{seg_x:.1f}" y="{y}" width="{seg_w:.1f}" height="10" rx="5" fill="{color}"/>')
        seg_x += seg_w
    y += 30
    for name, frac, color in langs:
        pct = f"{frac * 100:.1f}%"
        body_lines.append(
            f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{color}"/>'
            f'<text x="{x + 18}" y="{y}" fill="{TEXT}" font-size="12">{html.escape(name)}</text>'
            f'<text x="{x + 160}" y="{y}" fill="{TEXT}" font-size="12">{pct}</text>'
        )
        y += 24
    return card_shell(465, y + 15, "Top Languages", "\n  ".join(body_lines))


def render_activity_graph(user, weeks_back=20):
    calendar = user["contributionsCollection"]["contributionCalendar"]
    all_days = []
    for week in calendar["weeks"]:
        for d in week["contributionDays"]:
            all_days.append((datetime.date.fromisoformat(d["date"]), d["contributionCount"]))
    all_days.sort(key=lambda x: x[0])

    # roll up into weekly totals for the last N weeks
    weekly = defaultdict(int)
    for date, count in all_days:
        iso_year, iso_week, _ = date.isocalendar()
        weekly[(iso_year, iso_week)] += count
    keys = sorted(weekly.keys())[-weeks_back:]
    values = [weekly[k] for k in keys]
    max_val = max(values) if values and max(values) > 0 else 1

    width, height = 940, 260
    pad_left, pad_right, pad_top, pad_bottom = 40, 20, 40, 30
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    n = max(len(values), 1)
    step = plot_w / n

    points = []
    for i, v in enumerate(values):
        x = pad_left + i * step + step / 2
        y = pad_top + plot_h - (v / max_val) * plot_h
        points.append((x, y))

    path_d = ""
    if points:
        path_d = f"M {pad_left},{pad_top + plot_h} L " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        path_d += f" L {pad_left + plot_w:.1f},{pad_top + plot_h} Z"
    line_d = ""
    if points:
        line_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    dots = "\n  ".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{ICON}"/>' for x, y in points)

    body = f"""
  <path d="{path_d}" fill="{ICON}" opacity="0.15"/>
  <path d="{line_d}" fill="none" stroke="{ICON}" stroke-width="2"/>
  {dots}
  <line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{pad_left + plot_w}" y2="{pad_top + plot_h}" stroke="{BORDER}"/>
  """
    return card_shell(width, height, "Contribution Activity (weekly, last 20 weeks)", body)


def main():
    user = fetch_data(GH_USERNAME)
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(os.path.join(OUT_DIR, "github-stats-dark.svg"), "w") as f:
        f.write(render_stats_card(user))

    with open(os.path.join(OUT_DIR, "top-langs-dark.svg"), "w") as f:
        f.write(render_top_langs_card(user))

    with open(os.path.join(OUT_DIR, "activity-graph-dark.svg"), "w") as f:
        f.write(render_activity_graph(user))

    print(f"Wrote SVGs to {OUT_DIR}/")


if __name__ == "__main__":
    main()
