"""Regression suite for near-duplicate detection.

MERGE pairs are the same story told by two outlets. SEPARATE pairs are
different stories that share vocabulary and must never collapse.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from briefing.config import Config
from briefing.dedupe import _similar, sig

MERGE = [
    ("CISA issues Emergency Directive over actively exploited Ivanti Connect Secure zero-day",
     "Ivanti Connect Secure zero-day exploited by suspected nation-state actors, CISA warns"),
    ("Emergency directive issued as Ivanti Connect Secure zero-day comes under mass exploitation",
     "CISA issues Emergency Directive over actively exploited Ivanti Connect Secure zero-day"),
    ("Swiss rail giant Stadler rejects $12.3M ransom demand after cyberattack",
     "Stadler Rail refuses $12.3M extortion payment following ransomware breach"),
    ("OpenAI acquires enterprise analytics startup Metriq for $3.2 billion",
     "OpenAI buys analytics firm Metriq in $3.2B deal to bolster enterprise stack"),
    ("EU AI Act enforcement begins for general-purpose models",
     "Europe starts enforcing the AI Act against general purpose model providers"),
    ("Check Point warns of SmartConsole zero-day exploited in attacks",
     "Check Point patches SmartConsole zero day used in active attacks"),
    ("OpenAI raises $40B at a $340B valuation",
     "OpenAI closes $40B round at $340B valuation"),
    ("Russian hackers exploit Zimbra zero-click flaw for email theft",
     "Zimbra zero-click flaw exploited by Russian hackers to steal email"),
    ("EU fines Google $1 billion for antitrust violations",
     "Google hit with $1 billion EU antitrust fine"),
    ("New Dolphin X malware uses AI to rank high-value targets",
     "Dolphin X malware leverages AI to rank high value targets"),
]

SEPARATE = [
    ("CISA adds Ivanti flaw to KEV catalog", "CISA adds Citrix flaw to KEV catalog"),
    ("Check Point warns of SmartConsole zero-day exploited in attacks",
     "Fortinet warns of FortiGate zero-day exploited in attacks"),
    ("OpenAI raises $40B at a $340B valuation", "OpenAI launches GPT-6 for enterprise customers"),
    ("Microsoft 365 outage affects Teams and SharePoint", "Microsoft acquires AI startup for $2 billion"),
    ("Anthropic launches Claude for financial services", "Google launches Gemini for healthcare"),
    ("South Korea discloses data breach impacting diplomats",
     "Australian energy provider Origin says data breach exposes client data"),
    ("Nvidia reports record data center revenue", "AMD reports record data center revenue"),
    ("Ransomware gang hits Stadler Rail", "Ransomware gang hits Siemens Mobility"),
    ("Microsoft patches 60 flaws on Patch Tuesday", "Microsoft warns of Exchange zero-day"),
    ("OpenAI launches Sora 3 video model", "OpenAI launches GPT-6 for enterprise customers"),
    ("Payment processor breach exposes 4.2 million card records",
     "Cyber insurance premiums climb as ransomware claims accelerate"),
]


def main():
    cfg = Config()
    j, s = cfg.path("dedupe.jaccard_threshold"), cfg.path("dedupe.sequence_threshold")
    failures = 0
    for a, b in MERGE:
        if not _similar(sig(a), sig(b), j, s):
            failures += 1
            print("FAIL  should merge:\n      %s\n      %s" % (a, b))
    for a, b in SEPARATE:
        if _similar(sig(a), sig(b), j, s):
            failures += 1
            print("FAIL  should stay separate:\n      %s\n      %s" % (a, b))
    total = len(MERGE) + len(SEPARATE)
    print("dedupe: %d/%d cases pass" % (total - failures, total))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
