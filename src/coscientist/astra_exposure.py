"""Outcome-blind textual exposure construction for the Astra event study.

The primary treatment is deliberately transparent: a normalized count of a
frozen AI lexicon in the firm's latest eligible pre-event annual filing. No
market outcome, event-window return, CAR, winner/loser label, or post-cutoff
text is read here.

Design rationale:
* full annual-filing text is primary so 10-K, 20-F and 40-F issuers remain in a
  common treatment universe;
* Item 1/Item 4 business-section intensity and Item 1A/Item 3 risk-section
  intensity are secondary validation measures when section segmentation exists;
* a frontier/generative subset is secondary and does not determine the primary
  treatment;
* overlapping lexical spans are counted once, preferring the longest match, so
  e.g. ``generative AI`` is not double-counted as both ``generative AI`` and
  standalone ``AI``.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")

# Frozen before outcome access. Patterns are intentionally narrow and auditable.
# Standalone AI/LLM are case-sensitive to reduce ordinary-word false positives.
CORE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("generative_artificial_intelligence", re.compile(r"\bgenerative\s+artificial\s+intelligence\b", re.I)),
    ("generative_ai", re.compile(r"\bgenerative\s+AI\b")),
    ("gen_ai", re.compile(r"\bgen[-\s]?AI\b", re.I)),
    ("artificial_intelligence", re.compile(r"\bartificial\s+intelligence\b", re.I)),
    ("large_language_model", re.compile(r"\blarge\s+language\s+models?\b", re.I)),
    ("foundation_model", re.compile(r"\bfoundation\s+models?\b", re.I)),
    ("machine_learning", re.compile(r"\bmachine\s+learning\b", re.I)),
    ("deep_learning", re.compile(r"\bdeep\s+learning\b", re.I)),
    ("natural_language_processing", re.compile(r"\bnatural\s+language\s+processing\b", re.I)),
    ("computer_vision", re.compile(r"\bcomputer\s+vision\b", re.I)),
    ("neural_network", re.compile(r"\bneural\s+networks?\b", re.I)),
    ("ai_compound", re.compile(r"\bAI[-\s]+(?:powered|enabled|driven|based|assisted|generated|related|model|models|system|systems|technology|technologies)\b", re.I)),
    ("llm", re.compile(r"(?<![A-Za-z])LLMs?(?![A-Za-z])")),
    ("ai_dotted", re.compile(r"(?<![A-Za-z])A\.I\.(?![A-Za-z])")),
    ("ai", re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])")),
)

FRONTIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("generative_artificial_intelligence", re.compile(r"\bgenerative\s+artificial\s+intelligence\b", re.I)),
    ("generative_ai", re.compile(r"\bgenerative\s+AI\b")),
    ("gen_ai", re.compile(r"\bgen[-\s]?AI\b", re.I)),
    ("large_language_model", re.compile(r"\blarge\s+language\s+models?\b", re.I)),
    ("foundation_model", re.compile(r"\bfoundation\s+models?\b", re.I)),
    ("llm", re.compile(r"(?<![A-Za-z])LLMs?(?![A-Za-z])")),
    ("openai", re.compile(r"\bOpenAI\b", re.I)),
    ("chatgpt", re.compile(r"\bChatGPT\b", re.I)),
    ("gpt_model", re.compile(r"\bGPT[-\s]?[0-9]+(?:\.[0-9]+)?\b", re.I)),
)


@dataclass(frozen=True)
class MentionStats:
    words: int
    mentions: int
    per_10k_words: float
    log1p_per_10k: float
    any_mention: bool


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def _non_overlapping_matches(
    text: str,
    patterns: Iterable[tuple[str, re.Pattern[str]]],
) -> list[tuple[int, int, str]]:
    """Return non-overlapping matches, preferring the longest span.

    Sorting by start then negative length ensures a longer phrase wins over a
    nested shorter token beginning at the same point. Any later span that
    overlaps an accepted span is ignored.
    """
    candidates: list[tuple[int, int, str]] = []
    for name, pattern in patterns:
        for match in pattern.finditer(text or ""):
            candidates.append((match.start(), match.end(), name))
    candidates.sort(key=lambda x: (x[0], -(x[1] - x[0]), x[2]))

    accepted: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, name in candidates:
        if start < last_end:
            continue
        accepted.append((start, end, name))
        last_end = end
    return accepted


def mention_count(text: str, *, frontier: bool = False) -> int:
    patterns = FRONTIER_PATTERNS if frontier else CORE_PATTERNS
    return len(_non_overlapping_matches(text or "", patterns))


def term_counts(text: str, *, frontier: bool = False) -> dict[str, int]:
    patterns = FRONTIER_PATTERNS if frontier else CORE_PATTERNS
    out: dict[str, int] = {name: 0 for name, _ in patterns}
    for _, _, name in _non_overlapping_matches(text or "", patterns):
        out[name] = out.get(name, 0) + 1
    return out


def stats(text: str, *, frontier: bool = False) -> MentionStats:
    words = word_count(text)
    mentions = mention_count(text, frontier=frontier)
    rate = (mentions / words * 10_000.0) if words else 0.0
    return MentionStats(
        words=words,
        mentions=mentions,
        per_10k_words=rate,
        log1p_per_10k=math.log1p(rate),
        any_mention=mentions > 0,
    )


def standardized(values: Iterable[float]) -> list[float]:
    vals = [float(x) for x in values]
    if not vals:
        return []
    mean = sum(vals) / len(vals)
    var = sum((x - mean) ** 2 for x in vals) / len(vals)
    sd = math.sqrt(var)
    if not math.isfinite(sd) or sd <= 0:
        raise ValueError("cannot standardize a zero-variance/non-finite exposure")
    return [(x - mean) / sd for x in vals]
