"""Synthetic feed payloads that mimic the ten real sources, including the same
story appearing in several feeds with different headlines."""
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


def rfc822(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(title_of_feed, entries):
    items = "".join("""
 <item>
  <title>%s</title>
  <link>%s</link>
  <pubDate>%s</pubDate>
  <guid>%s</guid>
  <description><![CDATA[%s]]></description>
 </item>""" % (t, l, rfc822(h), l, d) for t, l, h, d in entries)
    return ("""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>%s</title><link>http://x</link>%s</channel></rss>"""
            % (title_of_feed, items)).encode()


FEEDS = {
 "CISA Advisories": rss("CISA", [
   ("CISA issues Emergency Directive over actively exploited Ivanti Connect Secure zero-day",
    "https://cisa.gov/news/ed-26-03-ivanti?utm_source=feed", 3,
    "CVE-2026-21893 carries a CVSS 9.8 rating and permits unauthenticated remote code execution. Federal agencies must patch within 48 hours."),
   ("CISA adds three Citrix NetScaler flaws to the Known Exploited Vulnerabilities catalog",
    "https://cisa.gov/news/kev-citrix-netscaler", 9,
    "Three Citrix NetScaler vulnerabilities were added to the KEV catalog following confirmed exploitation."),
 ]),
 "Krebs on Security": rss("Krebs", [
   ("Ivanti Connect Secure zero-day exploited by suspected nation-state actors, CISA warns",
    "https://krebsonsecurity.com/2026/07/ivanti-zero-day/", 4,
    "Investigators link the Ivanti Connect Secure exploitation campaign to a suspected nation-state group targeting government networks."),
   ("Payment processor breach exposes 4.2 million card records",
    "https://krebsonsecurity.com/2026/07/processor-breach/", 14,
    "A payment processor disclosed a data breach exposing 4.2 million card records; class action lawyers have already filed."),
 ]),
 "BleepingComputer": rss("BleepingComputer", [
   ("Emergency directive issued as Ivanti Connect Secure zero-day comes under mass exploitation",
    "https://bleepingcomputer.com/news/security/ivanti-ed-2603/", 5,
    "Mass exploitation of the Ivanti flaw is under way. CISA issued an emergency directive."),
   ("Swiss rail giant Stadler rejects $12.3M ransom demand after cyberattack",
    "https://bleepingcomputer.com/news/security/stadler-ransom/", 22,
    "The Everest ransomware gang demanded roughly $12.3 million after breaching a supplier data exchange platform."),
   ("Best VPN deals of the day - sponsored content",
    "https://bleepingcomputer.com/deals/vpn/", 6,
    "Sponsored discount roundup and coupon offers for VPN subscriptions."),
 ]),
 "The Hacker News": rss("THN", [
   ("Stadler Rail refuses $12.3M extortion payment following ransomware breach",
    "https://thehackernews.com/2026/07/stadler.html", 21,
    "Stadler confirmed the Everest gang's extortion attempt and refused to pay."),
   ("New msaRAT malware routes command-and-control traffic through Chrome and Edge",
    "https://thehackernews.com/2026/07/msarat.html", 26,
    "The Chaos ransomware group uses a backdoor that hides C2 traffic inside browser processes."),
 ]),
 "Dark Reading": rss("DarkReading", [
   ("SEC disclosure rule forces CISOs to report material incidents within four days",
    "https://darkreading.com/cyber-risk/sec-disclosure-four-days", 7,
    "New compliance and governance obligations shift board-level reporting expectations and raise third-party risk scrutiny."),
   ("Cyber insurance premiums climb as ransomware claims accelerate",
    "https://darkreading.com/cyber-risk/insurance-premiums", 30,
    "Insurers tighten underwriting; security budget owners face higher costs."),
 ]),
 "TechCrunch AI": rss("TechCrunch", [
   ("OpenAI acquires enterprise analytics startup Metriq for $3.2 billion",
    "https://techcrunch.com/2026/07/27/openai-metriq/?utm_medium=rss", 2,
    "The acquisition expands OpenAI's enterprise data platform and follows heavy data center capex commitments."),
   ("Anthropic launches Claude for regulated industries with on-premise deployment",
    "https://techcrunch.com/2026/07/26/claude-regulated/", 11,
    "Targets financial services and healthcare customers with compliance and governance controls."),
 ]),
 "VentureBeat AI": rss("VentureBeat", [
   ("OpenAI buys analytics firm Metriq in $3.2B deal to bolster enterprise stack",
    "https://venturebeat.com/ai/openai-metriq-acquisition/", 2,
    "Enterprise adoption push continues with a $3.2 billion acquisition."),
   ("Survey: 61% of enterprises report measurable ROI from AI agent deployments",
    "https://venturebeat.com/ai/agent-roi-survey/", 16,
    "CIOs report productivity gains, though governance and procurement remain obstacles."),
 ]),
 "MIT Tech Review AI": rss("MITTR", [
   ("EU AI Act enforcement begins for general-purpose models",
    "https://technologyreview.com/2026/07/26/eu-ai-act-enforcement/", 13,
    "Regulators set compliance deadlines; non-compliance risks substantial fines for enterprise deployments."),
 ]),
 "The Verge AI": rss("Verge", [
   ("Europe starts enforcing the AI Act against general purpose model providers",
    "https://theverge.com/2026/7/26/eu-ai-act-enforcement", 12,
    "The European Commission begins enforcement against general purpose AI model providers."),
   ("Nvidia unveils next-generation data center GPU with 40% efficiency gain",
    "https://theverge.com/2026/7/25/nvidia-gpu", 28,
    "New chip targets AI data center capex budgets."),
 ]),
 "Ars Technica AI": rss("Ars", [
   ("Researchers publish benchmark showing prompt formatting affects accuracy",
    "https://arstechnica.com/ai/2026/07/prompt-formatting-benchmark/", 34,
    "A minor benchmark study on prompt formatting sensitivity."),
   ("Old story that should be filtered out by the recency window",
    "https://arstechnica.com/ai/2026/06/ancient/", 400,
    "This item is far outside the lookback window."),
 ]),
}
