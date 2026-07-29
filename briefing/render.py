"""Email rendering: one message, two tables, inline CSS for client compatibility."""
from __future__ import annotations
from datetime import datetime
from html import escape
from urllib.parse import urlsplit

from .config import DOMAIN_LABEL

PRIORITY_STYLE = {
    "P1": ("#b3261e", "#fdecea", "Act today"),
    "P2": ("#8a5300", "#fff4e0", "Review"),
    "P3": ("#3f4a56", "#eef1f4", "Monitor"),
}
ACCENT = {"cyber": "#b3261e", "ai": "#1a56b3"}

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif")


def _host(url):
    try:
        h = urlsplit(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except ValueError:
        return ""


def _details(item):
    """Short, decision-useful supporting text."""
    # Claude's line, when present, already states what happened and why it
    # matters - it replaces the feed blurb rather than being appended to it.
    summary = (item.get("claude_details") or item.get("summary") or "").strip()
    low = summary.lower()

    # Only surface structured markers the prose does not already state, so the
    # cell reads as one sentence rather than repeating itself.
    bits = []
    missing_cves = [c for c in (item.get("cves") or []) if c.lower() not in low]
    if missing_cves:
        bits.append(", ".join(missing_cves))
    if item.get("cvss") and ("cvss" not in low):
        bits.append("CVSS %s" % item["cvss"])
    if summary:
        if len(summary) > 210:
            cut = summary[:210]
            if " " in cut[120:]:
                cut = cut[: cut.rfind(" ")]
            summary = cut.rstrip(" ,;:-–—.") + "…"
        bits.append(summary)
    if not bits:
        bits.append("No summary supplied by the source feed.")
    text = " · ".join(bits)
    extras = []
    if item.get("also_in"):
        extras.append("Also reported by %s" % ", ".join(item["also_in"][:3]))
    if item.get("author"):
        extras.append(item["author"])
    return text, extras


def _time_label(item, tz):
    pub = item.get("published")
    if not pub or item.get("published_missing"):
        return ""
    return pub.astimezone(tz).strftime("%d %b, %H:%M")


def _row(item, tz, idx):
    fg, bg, label = PRIORITY_STYLE[item["priority"]]
    zebra = "#ffffff" if idx % 2 == 0 else "#fafbfc"
    td = ("padding:12px 12px;border-bottom:1px solid #e6e9ec;"
          "font-family:%s;font-size:13px;line-height:1.5;color:#1f2933;"
          "vertical-align:top;background:%s;" % (FONT, zebra))
    details, extras = _details(item)
    extra_html = ""
    if extras:
        extra_html = ("<div style=\"margin-top:5px;font-size:11px;color:#7b8794;\">%s</div>"
                      % escape(" · ".join(extras)))
    when = _time_label(item, tz)
    when_html = ("<div style=\"margin-top:4px;font-size:11px;color:#7b8794;\">%s</div>" % escape(when)) if when else ""

    return """
      <tr>
        <td style="%s white-space:nowrap;">
          <span style="display:inline-block;padding:3px 9px;border-radius:11px;background:%s;color:%s;font-size:11px;font-weight:700;letter-spacing:.3px;">%s</span>
          <div style="margin-top:5px;font-size:10px;color:#7b8794;text-transform:uppercase;letter-spacing:.4px;">%s</div>
        </td>
        <td style="%s"><strong style="color:#1f2933;">%s</strong>%s</td>
        <td style="%s"><a href="%s" style="color:#12283f;text-decoration:none;font-weight:600;">%s</a></td>
        <td style="%s color:#4a5560;">%s%s</td>
        <td style="%s white-space:nowrap;">
          <a href="%s" style="display:inline-block;padding:7px 13px;border:1px solid #c9d1d9;border-radius:5px;color:#12283f;text-decoration:none;font-size:12px;font-weight:600;background:#ffffff;">Open&nbsp;&rsaquo;</a>
          <div style="margin-top:5px;font-size:10px;color:#9aa5b1;">%s</div>
        </td>
      </tr>""" % (
        td, bg, fg, item["priority"], escape(label),
        td, escape(item["source"]), when_html,
        td, escape(item["link"], quote=True), escape(item["title"]),
        td, escape(details), extra_html,
        td, escape(item["link"], quote=True), escape(_host(item["link"])),
    )


def _table(domain, items, tz):
    accent = ACCENT[domain]
    label = DOMAIN_LABEL[domain]
    counts = {p: sum(1 for i in items if i["priority"] == p) for p in ("P1", "P2", "P3")}

    if not items:
        body = ("<tr><td colspan=\"5\" style=\"padding:22px;font-family:%s;font-size:13px;"
                "color:#7b8794;background:#ffffff;\">Nothing new in this category since the last briefing.</td></tr>" % FONT)
    else:
        body = "".join(_row(it, tz, n) for n, it in enumerate(items))

    th = ("padding:10px 12px;font-family:%s;font-size:10px;font-weight:700;color:#ffffff;"
          "text-transform:uppercase;letter-spacing:.7px;text-align:left;background:%s;" % (FONT, accent))

    return """
    <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:0 0 34px 0;border:1px solid #e6e9ec;border-radius:8px;overflow:hidden;">
      <tr><td colspan="5" style="padding:15px 14px 13px 14px;background:%s;">
        <div style="font-family:%s;font-size:16px;font-weight:700;color:#ffffff;letter-spacing:.2px;">%s</div>
        <div style="font-family:%s;font-size:11px;color:rgba(255,255,255,.82);margin-top:4px;">%d item%s &nbsp;|&nbsp; %d P1 &nbsp;|&nbsp; %d P2 &nbsp;|&nbsp; %d P3</div>
      </td></tr>
      <tr>
        <th style="%s width:9%%;">Priority</th>
        <th style="%s width:13%%;">Source</th>
        <th style="%s width:29%%;">Short Information</th>
        <th style="%s width:37%%;">Additional Details</th>
        <th style="%s width:12%%;">Link</th>
      </tr>
      %s
    </table>""" % (accent, FONT, escape(label), FONT, len(items), "" if len(items) == 1 else "s",
                   counts["P1"], counts["P2"], counts["P3"],
                   th, th, th, th, th, body)


def render_html(tables, meta, tz):
    now = meta["generated_at"].astimezone(tz)
    total = sum(len(v) for v in tables.values())

    feed_bits = []
    for f in meta.get("feed_status", []):
        colour = "#3f8f4a" if f["status"] in ("ok", "not_modified") else "#b3261e"
        feed_bits.append('<span style="color:%s;">%s</span>' % (colour, escape(f["name"])))
    feed_line = " &nbsp;·&nbsp; ".join(feed_bits)

    cur = meta.get("curation") or {}
    if cur.get("used"):
        ranking_line = ("Prioritised and summarised by %s."
                        % escape(str(cur.get("model", "Claude"))))
    else:
        ranking_line = ("Ranked by keyword scoring &mdash; Claude ranking unavailable (%s)."
                        % escape(str(cur.get("error", "not configured"))))

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Briefing</title>
<style>
  @media only screen and (max-width:620px) {
    .wrap { padding:10px !important; }
    td, th { font-size:12px !important; padding:8px !important; }
  }
</style></head>
<body style="margin:0;padding:0;background:#f4f6f8;">
<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f6f8;">
<tr><td align="center" class="wrap" style="padding:26px 14px;">
<table role="presentation" width="1000" cellpadding="0" cellspacing="0" border="0" style="max-width:1000px;width:100%%;background:#ffffff;border-radius:10px;border:1px solid #e2e6ea;">
  <tr><td style="padding:26px 26px 8px 26px;border-bottom:1px solid #eef1f4;">
    <div style="font-family:%s;font-size:21px;font-weight:700;color:#12283f;letter-spacing:-.2px;">Daily Intelligence Briefing</div>
    <div style="font-family:%s;font-size:13px;color:#5c6773;margin-top:6px;">%s &nbsp;·&nbsp; %d item%s across Cyber Security and AI</div>
    <div style="font-family:%s;font-size:11px;color:#8894a0;margin-top:10px;padding-bottom:16px;">
      Window: last %s &nbsp;|&nbsp; %d fetched &nbsp;|&nbsp; %d cross-source duplicates merged &nbsp;|&nbsp; %d previously sent suppressed
    </div>
  </td></tr>
  <tr><td style="padding:24px 26px 4px 26px;">
    %s
    %s
  </td></tr>
  <tr><td style="padding:6px 26px 24px 26px;border-top:1px solid #eef1f4;">
    <div style="font-family:%s;font-size:11px;color:#8894a0;line-height:1.7;">
      <strong style="color:#5c6773;">Feeds:</strong> %s
      <br>%s Priority reflects operational urgency for a director audience: <strong>P1</strong> act today, <strong>P2</strong> review this week, <strong>P3</strong> monitor.
      <br>Generated %s &nbsp;·&nbsp; run <code style="color:#8894a0;">%s</code>
    </div>
  </td></tr>
</table>
</td></tr></table>
</body></html>""" % (
        FONT, FONT, now.strftime("%A, %d %B %Y"), total, "" if total == 1 else "s",
        FONT, meta.get("window_label", "24h"), meta.get("fetched", 0),
        meta.get("merged", 0), meta.get("suppressed", 0),
        _table("cyber", tables["cyber"], tz),
        _table("ai", tables["ai"], tz),
        FONT, feed_line, ranking_line, now.strftime("%H:%M %Z"), escape(meta.get("run_id", "")),
    )


def render_text(tables, meta, tz):
    now = meta["generated_at"].astimezone(tz)
    lines = ["DAILY INTELLIGENCE BRIEFING", now.strftime("%A, %d %B %Y"), ""]
    for domain in ("cyber", "ai"):
        items = tables[domain]
        lines += [DOMAIN_LABEL[domain].upper(), "=" * len(DOMAIN_LABEL[domain]), ""]
        if not items:
            lines += ["  Nothing new since the last briefing.", ""]
            continue
        for it in items:
            details, extras = _details(it)
            lines.append("[%s] %s" % (it["priority"], it["title"]))
            lines.append("       Source : %s%s" % (it["source"],
                         ("  (" + _time_label(it, tz) + ")") if _time_label(it, tz) else ""))
            lines.append("       Details: %s" % details)
            if extras:
                lines.append("       Note   : %s" % " · ".join(extras))
            lines.append("       Link   : %s" % it["link"])
            lines.append("")
    cur = meta.get("curation") or {}
    lines.append("Prioritised by %s." % cur.get("model", "Claude") if cur.get("used")
                 else "Ranked by keyword scoring (Claude unavailable: %s)." % cur.get("error", "not configured"))
    lines.append("Priority: P1 act today | P2 review this week | P3 monitor.")
    lines.append("Run %s · %d fetched · %d duplicates merged · %d previously sent suppressed"
                 % (meta.get("run_id", ""), meta.get("fetched", 0),
                    meta.get("merged", 0), meta.get("suppressed", 0)))
    return "\n".join(lines)
