"""The privacy gate: scan every text that reaches the screen or the voice before a video is shared.

Four kinds of hit, each fails the build:
  deny      your own pattern from $TRUETAKE_DENY_PATTERN (a Python regular expression, matched
            case-insensitively): legal names, client names, anything you never want published. The
            pattern lives in your environment, not in any file truetake writes.
  home      a home-directory path: $HOME itself, or a Linux, macOS or Windows user folder.
  username  the login name of the account running truetake, as a whole word.
  email     an email address, unless it matches `privacy.allow_emails` in truetake.yaml.
"""
from __future__ import annotations

import fnmatch
import getpass
import os
import re
from dataclasses import dataclass

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Linux and macOS user folders, and Windows ones with either slash.
HOME_RE = re.compile(r"(?<![\w.])/(?:home|Users)/[^/\s]+|[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+", re.IGNORECASE)


@dataclass(frozen=True)
class Hit:
    source: str
    line: int
    kind: str
    snippet: str  # the line with the match replaced by [kind], so the report never repeats it


@dataclass
class Scanner:
    deny: re.Pattern | None
    home: str
    username: str
    allow_emails: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, allow_emails: list[str] | tuple[str, ...] = ()) -> "Scanner":
        pattern = os.environ.get("TRUETAKE_DENY_PATTERN", "").strip()
        try:
            deny = re.compile(pattern, re.IGNORECASE) if pattern else None
        except re.error as e:
            raise ValueError(f"$TRUETAKE_DENY_PATTERN is not a valid regular expression: {e}") from e
        try:
            user = getpass.getuser()
        except Exception:  # no login name available (some containers)
            user = ""
        return cls(deny=deny, home=os.path.expanduser("~"), username=user, allow_emails=tuple(allow_emails))

    def _email_allowed(self, email: str) -> bool:
        return any(fnmatch.fnmatch(email.lower(), p.lower()) for p in self.allow_emails)

    def scan(self, source: str, text: str) -> list[Hit]:
        hits: list[Hit] = []
        user_re = re.compile(rf"(?<![A-Za-z0-9]){re.escape(self.username)}(?![A-Za-z0-9])", re.IGNORECASE) if self.username else None
        for n, line in enumerate(text.splitlines(), 1):
            found: list[tuple[str, re.Match]] = []
            if self.deny:
                found += [("deny", m) for m in self.deny.finditer(line) if m.group(0)]
            if self.home and self.home != "/" and self.home in line:
                i = line.index(self.home)
                found.append(("home", _Span(i, i + len(self.home))))
            found += [("home", m) for m in HOME_RE.finditer(line)]
            if user_re:
                found += [("username", m) for m in user_re.finditer(line)]
            found += [("email", m) for m in EMAIL_RE.finditer(line) if not self._email_allowed(m.group(0))]
            for kind in dict.fromkeys(k for k, _ in found):
                spans = sorted((m.start(), m.end()) for k, m in found if k == kind)
                hits.append(Hit(source, n, kind, _redact(line, spans, kind)))
        return hits

    def describe(self) -> dict:
        """What was checked, without repeating the pattern or the names themselves."""
        return {"deny_pattern_set": self.deny is not None, "home_checked": bool(self.home and self.home != "/"),
                "username_checked": bool(self.username), "allowed_emails": list(self.allow_emails)}


@dataclass(frozen=True)
class _Span:
    s: int
    e: int

    def start(self) -> int:
        return self.s

    def end(self) -> int:
        return self.e


def _redact(line: str, spans: list[tuple[int, int]], kind: str) -> str:
    out, last = "", 0
    for s, e in spans:
        if s < last:
            continue
        out += line[last:s] + f"[{kind}]"
        last = e
    out += line[last:]
    return out[:200]
