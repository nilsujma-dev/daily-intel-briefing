from __future__ import annotations
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


PLACEHOLDER_RECIPIENT = "you@example.com"


def _load(name):
    with open(ROOT / "config" / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_optional(name):
    path = ROOT / "config" / name
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _deep_merge(base, override):
    """Override wins, but only for the keys it actually mentions - so a local
    file naming just `recipient` keeps every other default intact, and new
    settings added upstream still reach existing installs."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Config:
    def __init__(self):
        # Tracked defaults, then untracked personal overrides. This is what
        # keeps a real recipient address out of a public repository.
        self.settings = _deep_merge(_load("settings.json"),
                                    _load_optional("settings.local.json"))
        feeds = _load("feeds.json")
        self.feeds = {
            "cyber": [f for f in feeds["cyber"] if f.get("enabled", True)],
            "ai": [f for f in feeds["ai"] if f.get("enabled", True)],
        }
        self.all_feeds = [dict(f, domain=d) for d in ("cyber", "ai") for f in self.feeds[d]]
        self.root = ROOT
        self.state_dir = ROOT / "state"
        self.state_dir.mkdir(exist_ok=True)
        (self.state_dir / "logs").mkdir(exist_ok=True)
        self.db_path = self.state_dir / "briefing.db"

    def path(self, key_path, default=None):
        cur = self.settings
        for part in key_path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def secret_path(self, key):
        return ROOT / self.path("oauth." + key)

    def require_recipient(self):
        """Fail loudly and usefully rather than emailing the placeholder."""
        who = (self.path("recipient") or "").strip()
        if not who or who == PLACEHOLDER_RECIPIENT:
            raise ValueError(
                "No recipient configured.\n"
                "  Create config/settings.local.json with your address:\n"
                '      {"recipient": "you@yourdomain.com"}\n'
                "  That file is git-ignored, so it never reaches the repository.")
        return who


DOMAIN_LABEL = {
    "cyber": "Enterprise Cyber Security Intelligence Briefing",
    "ai": "AI News Briefing",
}
