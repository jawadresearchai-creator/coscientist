"""Reusable SEC text corpus acquisition, robust HTML normalization, and section segmentation engine.

Strict information boundary:
- Operates outcome-blind on pre-event annual filings (10-K, 20-F, 40-F).
- Zero event-window return or outcome data accessed.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import lxml.html

logger = logging.getLogger(__name__)

USER_AGENT = "Management Science CoScientist research contact jawadresearch.ai@gmail.com"
SEC_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate"
}

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div", "dl", "dt",
    "figcaption", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "ul"
}

ITEM_10K_RE = re.compile(
    r"^\s*item\s+(1a|1b|1c|1|2|7a|7|8)(?![a-z0-9])(?:\s*[\.\:\-\u2013\u2014])?(?:\s+.*)?\s*$",
    re.I
)
TARGET_ENDS_10K = {
    "1": {"1a", "1b", "1c", "2"},
    "1a": {"1b", "1c", "2"},
    "7": {"7a", "8"}
}

ITEM_20F_RE = re.compile(
    r"^\s*item\s+(3(?:\.[a-z0-9]+)?|4(?:\.[a-z0-9]+)?|5(?:\.[a-z0-9]+)?|6|7|8)(?![a-z0-9])(?:\s*[\.\:\-\u2013\u2014])?(?:\s+.*)?\s*$",
    re.I
)
TARGET_ENDS_20F = {
    "3": {"4", "5"},
    "4": {"5", "6"},
    "5": {"6", "7"}
}


class TokenBucketLimiter:
    """Thread-safe rate limiter compliant with SEC guidelines (<= 7.5 req/s)."""
    def __init__(self, max_per_second: float = 7.5) -> None:
        self.interval = 1.0 / max_per_second
        self.lock = threading.Lock()
        self.next_time = time.time()

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            if self.next_time <= now:
                target = now
                self.next_time = now + self.interval
            else:
                target = self.next_time
                self.next_time += self.interval
        sleep_time = target - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)


def html_to_normalized_text(raw_bytes: bytes) -> Tuple[str, str, str]:
    """
    Converts raw SEC HTML/text bytes to normalized plain text.
    Injects newlines for block elements so line-based section matching succeeds.
    Returns: (normalized_text, extraction_status, failure_reason)
    """
    if not raw_bytes or len(raw_bytes) < 50:
        return "", "FAILED_MALFORMED_SOURCE", "RAW_PAYLOAD_EMPTY_OR_TOO_SHORT"

    try:
        tree = lxml.html.fromstring(raw_bytes)
        for bad in tree.xpath("//script|//style|//noscript|//head"):
            bad.drop_tree()
            
        # Inject newlines after block tags
        for el in tree.xpath("//p|//div|//tr|//h1|//h2|//h3|//h4|//h5|//h6|//li|//br|//hr"):
            el.tail = "\n" + (el.tail or "")

        text = tree.text_content().replace("\xa0", " ")
        lines = []
        for line in text.split("\n"):
            clean = re.sub(r"[ \t\f\v]+", " ", line).strip()
            if clean:
                lines.append(clean)
                
        clean_text = "\n".join(lines).strip()
    except Exception as exc:
        try:
            raw_str = raw_bytes.decode("utf-8", errors="replace")
            raw_str = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", raw_str, flags=re.I)
            raw_str = re.sub(r"<[^>]+>", "\n", raw_str)
            lines = [re.sub(r"[ \t\f\v]+", " ", l).strip() for l in raw_str.split("\n") if l.strip()]
            clean_text = "\n".join(lines).strip()
        except Exception as e2:
            return "", "FAILED_TEXT_EXTRACTION", f"PARSER_EXCEPTION: {exc}; FALLBACK_FAILED: {e2}"

    n_chars = len(clean_text)
    if n_chars >= 5000:
        return clean_text, "SUCCESS_FULL_TEXT", "NONE"
    elif n_chars >= 500:
        return clean_text, "SUCCESS_PARTIAL_TEXT", "TEXT_UNDER_5000_CHARS"
    elif n_chars > 0:
        return clean_text, "FAILED_TEXT_EXTRACTION", "EXTRACTED_TEXT_TOO_SHORT_UNDER_500_CHARS"
    else:
        return "", "FAILED_TEXT_EXTRACTION", "EXTRACTED_TEXT_EMPTY"


def extract_filing_sections(text: str, form: str) -> Dict[str, Any]:
    """
    Extracts canonical business, risk, and MD&A sections from normalized text.
    Handles 10-K (Item 1, 1A, 7) and 20-F (Item 4, 3/3.D, 5).
    """
    lines = text.splitlines(keepends=True)
    is_20f = "20-F" in form.upper()
    is_40f = "40-F" in form.upper()

    if is_40f:
        return {
            "item1": "", "item1a": "", "item7": "",
            "item1_available": False, "item1a_available": False, "item7_available": False,
            "foreign_item4_available": False, "foreign_item3d_available": False, "foreign_item5_available": False,
            "segmentation_status": "UNSEGMENTED_FULL_TEXT_RETAINED",
            "missing_reason": "FORM_40F_CANADIAN_AIF_RETAINED_AS_FULL_TEXT"
        }

    regex = ITEM_20F_RE if is_20f else ITEM_10K_RE
    target_ends = TARGET_ENDS_20F if is_20f else TARGET_ENDS_10K

    headings = []
    pos = 0
    for line in lines:
        clean = line.strip()
        if clean and len(clean) <= 260:
            m = regex.match(clean)
            if m:
                token = m.group(1).lower().split(".")[0]
                headings.append((token, pos, clean))
        pos += len(line)

    sections = {}
    targets = ["4", "3", "5"] if is_20f else ["1", "1a", "7"]
    for target in targets:
        starts = [h for h in headings if h[0] == target]
        ends = [h for h in headings if h[0] in target_ends.get(target, set())]
        candidates = []
        for s in starts:
            laters = [e for e in ends if e[1] > s[1]]
            if not laters:
                continue
            e = min(laters, key=lambda x: x[1])
            seg = text[s[1]:e[1]].strip()
            if len(seg) >= 200:
                candidates.append(seg)
        if candidates:
            candidates.sort(key=len, reverse=True)
            sections[target] = candidates[0]

    if is_20f:
        item4 = sections.get("4", "")
        item3 = sections.get("3", "")
        item5 = sections.get("5", "")
        avail_count = sum(bool(x) for x in (item4, item3, item5))
        if avail_count == 3:
            seg_status = "FULL_SECTIONS_SEGMENTED"
            miss_reason = "NONE"
        elif avail_count > 0:
            seg_status = "PARTIAL_SECTIONS_SEGMENTED"
            miss_reason = "PARTIAL_20F_ITEMS_FOUND"
        else:
            seg_status = "UNSEGMENTED_FULL_TEXT_RETAINED"
            miss_reason = "FORM_20F_UNSEGMENTED_RETAINED_AS_FULL_TEXT"

        return {
            "item1": item4, "item1a": item3, "item7": item5,
            "item1_available": bool(item4), "item1a_available": bool(item3), "item7_available": bool(item5),
            "foreign_item4_available": bool(item4), "foreign_item3d_available": bool(item3), "foreign_item5_available": bool(item5),
            "segmentation_status": seg_status,
            "missing_reason": miss_reason
        }
    else:
        item1 = sections.get("1", "")
        item1a = sections.get("1a", "")
        item7 = sections.get("7", "")
        avail_count = sum(bool(x) for x in (item1, item1a, item7))
        if avail_count == 3:
            seg_status = "FULL_SECTIONS_SEGMENTED"
            miss_reason = "NONE"
        elif avail_count > 0:
            seg_status = "PARTIAL_SECTIONS_SEGMENTED"
            miss_reason = "PARTIAL_10K_ITEMS_FOUND"
        else:
            seg_status = "UNSEGMENTED_FULL_TEXT_RETAINED"
            miss_reason = "10K_ITEMS_NOT_INDIVIDUALLY_DELIMITED"

        return {
            "item1": item1, "item1a": item1a, "item7": item7,
            "item1_available": bool(item1), "item1a_available": bool(item1a), "item7_available": bool(item7),
            "foreign_item4_available": False, "foreign_item3d_available": False, "foreign_item5_available": False,
            "segmentation_status": seg_status,
            "missing_reason": miss_reason
        }
