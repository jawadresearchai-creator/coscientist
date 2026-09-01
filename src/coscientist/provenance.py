"""Numeric provenance: every number in the manuscript must come from somewhere.

The manuscript bridge stops transcription errors. It does not stop invention.
A writer -- human or model -- can still put a number in a sentence that no
analysis produced, and prose is exactly where that is hardest to notice.

So this validator reads the finished manuscript, extracts every number, and
fails if any of them cannot be traced to a row in the results bundle. It is
the same discipline as the power gate's `pre_period_end`: the difference
between a promise in a docstring and a check that runs.

It errs toward false positives by design. A legitimate number the validator
does not recognise costs one line in an allow-list; a fabricated number that
slips through costs a retraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .manuscript import ResultsBundle

# A number, optionally signed, with thousands separators, decimals, exponent or %
NUMBER = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?%?")

TOKEN_CITATION = re.compile(r"\[\[R:([A-Za-z0-9_.-]+)(?:\|([A-Za-z_]+))?\]\]")
LITERAL_ESCAPE = re.compile(r"\{\{lit:([^}]+)\}\}")
# A declared, registered non-result number: an alpha level, a statutory fee, a
# figure quoted from a cited paper. Unlike {{lit:}} it names an entry that has
# to exist, so even numbers the analysis did not produce carry provenance.
LITERAL_CITATION = re.compile(r"\[\[L:([A-Za-z0-9_.-]+)\]\]")

# Named macros. `[[R:h1|estimate]]` binds a number to a record and a field, but
# nothing stops "The p-value was [[R:h1|estimate]]" -- the token is traceable
# and the sentence is false. Arbitrary language cannot be checked, so instead
# the correct citation is made the easy one to write: `[[P:h1]]` says what it
# is, and the cue check below closes the narrow, high-precision cases where the
# prose states a field outright and cites a different one.
MACRO = re.compile(r"\[\[(EST|CI|P|N|SE|CLUST)?:([A-Za-z0-9_.-]+)\]\]")
MACRO_FIELD = {"EST": "estimate", "CI": "ci", "P": "p",
               "N": "n", "SE": "se", "CLUST": "clusters"}

# Cues that state, unambiguously and immediately before the citation, which
# statistic is being reported. Anchored at the end of the preceding text so
# "robust to clustering, the estimate was ..." does not trip the cluster cue.
# Deliberately small: a wide vocabulary here would be a proximity heuristic,
# and proximity heuristics are what field tokens replaced.
SEMANTIC_CUES: list[tuple[re.Pattern, set[str]]] = [
    (re.compile(r"\bp[-\s]?values?\s*(?:was|is|of|:|=|<|>)?\s*$", re.I), {"p"}),
    (re.compile(r"(?:^|[\s(])p\s*[=<>]\s*$", re.I), {"p"}),
    (re.compile(r"\b(?:standard\s+error|s\.?e\.?)\s*(?:was|is|of|:|=)?\s*$", re.I), {"se"}),
    (re.compile(r"\b(?:N|n|sample\s+size|observations?)\s*(?:was|is|of|:|=)\s*$"), {"n"}),
    (re.compile(r"\b(?:confidence|credible)\s+interval\s*(?:was|is|of|:|=|,)?\s*$", re.I),
     {"ci", "ci_low", "ci_high"}),
    (re.compile(r"\b\d{2}%\s*(?:CI|credible\s+interval)\s*[:=,]?\s*$", re.I),
     {"ci", "ci_low", "ci_high"}),
    (re.compile(r"\b(?:clusters?)\s*(?:was|is|of|:|=)\s*$", re.I), {"clusters"}),
]

# Contexts where a bare number is structural, not a finding.
STRUCTURAL = re.compile(
    r"(?:table|figure|fig\.?|panel|section|sec\.?|appendix|equation|eq\.?|"
    r"chapter|step|stage|footnote|note|column|row|page|pp?\.)\s*$",
    re.IGNORECASE,
)
# A confidence level is a convention, not a finding: the 95 in "95% CI" is not
# something the analysis estimated. Matched on the text FOLLOWING the number.
CONF_LEVEL = re.compile(
    r"^\s*%?\s*(?:CI\b|confidence|credible|interval|level\b|power\b|significance)",
    re.IGNORECASE,
)

# "p < 0.001" and friends are threshold claims, checked against the bundle's p values.
P_THRESHOLD = re.compile(r"\bp\s*(<=|>=|<|>|≤|≥|=)\s*$", re.IGNORECASE)

# Scale conversion between a percentage and a proportion is only legitimate
# when the record is actually on that scale. Without this, 241 USD divided by
# 100 validated a claim of "2.41 pp" -- the validator was matching arithmetic
# coincidences across incompatible units.
PERCENTISH = re.compile(r"(^|[^a-z])(pp|%|percent|percentage|share|proportion|rate)([^a-z]|$)",
                        re.IGNORECASE)
YEAR = re.compile(r"^(?:19|20)\d{2}$")


@dataclass
class NumberClaim:
    raw: str
    value: float
    decimals: int
    line: int
    context: str
    kind: str = "value"            # value | p_threshold | structural | year | literal | token
    matched_token: str | None = None
    verdict: str = "UNCHECKED"     # MATCHED | UNMATCHED | SKIPPED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProvenanceError(RuntimeError):
    """Raised when the manuscript uses a construct strict mode forbids."""


@dataclass
class ProvenanceReport:
    total: int = 0
    matched: int = 0
    skipped: int = 0
    unmatched: list[NumberClaim] = field(default_factory=list)
    ambiguous: list[NumberClaim] = field(default_factory=list)
    claims: list[NumberClaim] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unmatched

    def summary(self) -> str:
        head = (
            f"{self.matched}/{self.total - self.skipped} checkable numbers traced "
            f"({self.skipped} skipped as structural)"
        )
        if self.ambiguous:
            head += f"; {len(self.ambiguous)} p-claim(s) bound by proximity on a "
            head += "multi-estimate line -- cite [[R:token|p]] to disambiguate"
        if self.ok:
            return head + " -- PASS"
        lines = [head + f" -- FAIL, {len(self.unmatched)} untraceable:"]
        for c in self.unmatched:
            lines.append(f"  line {c.line}: {c.raw}   ...{c.context.strip()}...")
        return "\n".join(lines)


def _parse(raw: str) -> tuple[float, int, bool]:
    """Return (value, decimals as written, was_percent)."""
    pct = raw.endswith("%")
    body = raw[:-1] if pct else raw
    body = body.replace(",", "")
    value = float(body)
    if "." in body and "e" not in body.lower():
        decimals = len(body.split(".")[1])
    else:
        decimals = 0
    return value, decimals, pct


UNIT_WORD = re.compile(r"^\s*(pp|%|percent|percentage points?|usd|dollars?|bp|basis points?|"
                       r"units?|days?|years?|quarters?|months?)\b", re.IGNORECASE)

_UNIT_FAMILY = {
    "pp": "percent", "%": "percent", "percent": "percent", "percentage": "percent",
    "percentage point": "percent", "percentage points": "percent",
    "usd": "money", "dollar": "money", "dollars": "money",
    "bp": "percent", "basis point": "percent", "basis points": "percent",
}


def _family(unit: str) -> str | None:
    return _UNIT_FAMILY.get(unit.strip().lower())


def _unit_after(after: str) -> str | None:
    m = UNIT_WORD.match(after)
    return m.group(1) if m else None


def _units_agree(record_units: str, written_unit: str) -> bool:
    """Fail closed. An unknown relationship is not evidence of compatibility.

    Rejecting only when BOTH units mapped to known families meant the small
    hardcoded vocabulary silently permitted everything outside it: 2.41 days
    validated 2.41 dollars, and 2.41 proportion validated 2.41 dollars, because
    one side was unrecognised. When two units are both declared and their
    relationship is unknown, the honest answer is no.
    """
    a, b = _family(record_units), _family(written_unit)
    if a and b:
        return a == b
    return record_units.strip().lower() == written_unit.strip().lower()


def _close(candidate: float, target: float, decimals: int) -> bool:
    """Does `target` round to `candidate` at the precision the author wrote?"""
    tol = 0.5 * (10 ** -decimals) + 1e-12
    return abs(round(target, decimals) - candidate) <= 1e-9 or abs(target - candidate) <= tol


def _semantic_mismatches(text: str) -> list[str]:
    """Where the prose states the statistic outright, the citation must match."""
    out: list[str] = []
    for m in TOKEN_CITATION.finditer(text):
        field = (m.group(2) or "").lower()
        if not field:
            continue
        before = text[max(0, m.start() - 60):m.start()]
        for pattern, allowed_fields in SEMANTIC_CUES:
            if pattern.search(before) and field not in allowed_fields:
                cue = before.strip().split("\n")[-1][-40:].strip()
                out.append(
                    f"...{cue}[[R:{m.group(1)}|{field}]] -- the text reports "
                    f"{'/'.join(sorted(allowed_fields))}, the citation is {field}"
                )
                break
    return out


def validate(
    text: str,
    bundle: ResultsBundle,
    *,
    allow: list[float] | None = None,
    skip_years: bool = True,
    require_tokens: bool = False,
    literals: dict[str, Any] | None = None,
) -> ProvenanceReport:
    """Check every number in `text` against `bundle`.

    `allow` holds numbers that legitimately are not results -- a policy
    threshold, a statutory fee, a count quoted from a cited paper. Keep it
    short and keep it explicit; a long allow-list is the validator being
    talked out of its job.

    `literals` is the registry backing `[[L:id]]` citations -- numbers that are
    legitimately not results but still need a stated reason.

    `require_tokens` is the strict mode and the one to prefer for confirmatory
    manuscripts. Bare-number matching only proves a number RESEMBLES some
    result; it cannot tell whether it is the RIGHT result, so a genuine 2.41
    from one endpoint will happily validate an unrelated 2.41 claim elsewhere.
    Requiring `[[R:token]]` for every result claim makes the association
    explicit rather than coincidental. The bare-number sweep then remains
    useful as a secondary anti-fabrication net.
    """
    allowed = set(allow or [])
    literals = literals or {}

    # Strict mode has exactly one exception mechanism, and it is the literal
    # registry. `--allow` is an unrestricted manual bypass: it takes a bare
    # number and waves it through with no id, no source and no record that it
    # happened. Keeping it available under `require_tokens` made the guarantee
    # "strict mode has no escape hatch" false the moment anyone used the flag.
    if require_tokens and allowed:
        raise ProvenanceError(
            f"strict mode forbids --allow (given {sorted(allowed)}). A number that "
            "is legitimately not a result still needs a stated origin: register it "
            "in the literal registry and cite it as [[L:id]]."
        )

    # Expand named macros into field tokens before anything else looks at the
    # text, so [[P:h1]] and [[R:h1|p]] are one code path from here on.
    text = expand_macros(text)
    pairs = bundle.all_numbers()
    report = ProvenanceReport()

    # {{lit:999.9}} was removed before any checking and never inspected, so
    # strict mode did not mean "every number is traceable" -- it meant "every
    # number outside a wrapper nobody validates". An unrestricted escape from a
    # provenance system is the provenance system.
    if require_tokens:
        escapes = LITERAL_ESCAPE.findall(text)
        if escapes:
            raise ProvenanceError(
                f"strict mode forbids {{{{lit:}}}} escapes; found {len(escapes)} "
                f"({', '.join(escapes[:3])}). Register the number in the literal "
                "registry and cite it as [[L:id]], or cite the result that produced it."
            )

    for lit_id in LITERAL_CITATION.findall(text):
        if lit_id not in literals:
            raise ProvenanceError(
                f"[[L:{lit_id}]] is not in the literal registry. Every number in a "
                "confirmatory manuscript needs a stated origin, results included."
            )

    for tok, fld in TOKEN_CITATION.findall(text):
        record = bundle.get(tok)            # raises if a cited token does not exist
        if fld:
            # render(), not field_value(): the latter returns None for a field
            # the record simply does not have, so citing [[R:t|se]] on a record
            # with no standard error passed silently and rendered nothing.
            record.render(fld)

    if require_tokens:
        mismatches = _semantic_mismatches(text)
        if mismatches:
            raise ProvenanceError(
                "the prose names one statistic and cites another:\n  - "
                + "\n  - ".join(mismatches)
                + "\n\nField tokens bind a number to a record and a field; they "
                  "cannot make the surrounding sentence true. Where the sentence "
                  "says outright which statistic it reports, the citation has to "
                  "agree. Prefer the named macros -- [[EST:t]], [[CI:t]], [[P:t]], "
                  "[[N:t]] -- which say what they are."
            )

    # Blank out token citations and explicit literals so they are not re-parsed.
    scrubbed = TOKEN_CITATION.sub(lambda m: " " * len(m.group(0)), text)
    scrubbed = LITERAL_CITATION.sub(lambda m: " " * len(m.group(0)), scrubbed)
    scrubbed = LITERAL_ESCAPE.sub(lambda m: " " * len(m.group(0)), scrubbed)

    for lineno, line in enumerate(scrubbed.splitlines(), start=1):
        # Tokens matched earlier on this line. A p-value written beside an
        # estimate is a claim ABOUT that estimate, so it must be satisfied by
        # that record -- not merely by some other record in the bundle that
        # happens to clear the threshold. Without this, "2.41 pp (p > 0.001)"
        # validated because an unrelated secondary contrast had p = 0.361.
        line_tokens: list[str] = []
        for m in NUMBER.finditer(line):
            raw = m.group(0)
            try:
                value, decimals, pct = _parse(raw)
            except ValueError:
                continue

            before = line[max(0, m.start() - 40):m.start()]
            after = line[m.end():m.end() + 24]
            unit_hint = _unit_after(after)
            context = line[max(0, m.start() - 40):min(len(line), m.end() + 40)]
            claim = NumberClaim(raw=raw, value=value, decimals=decimals,
                                line=lineno, context=context)
            report.total += 1
            report.claims.append(claim)

            if STRUCTURAL.search(before) or CONF_LEVEL.match(after):
                claim.kind, claim.verdict = "structural", "SKIPPED"
                report.skipped += 1
                continue
            if skip_years and decimals == 0 and not pct and YEAR.match(raw):
                claim.kind, claim.verdict = "year", "SKIPPED"
                report.skipped += 1
                continue
            if value in allowed:
                claim.kind, claim.verdict = "literal", "SKIPPED"
                report.skipped += 1
                continue

            if require_tokens:
                claim.kind, claim.verdict = "bare_number", "UNMATCHED"
                report.unmatched.append(claim)
                continue

            pm = P_THRESHOLD.search(before)
            if pm:
                # The operator is captured; use it. Testing `<` regardless of
                # what was written meant "p > 0.5" validated against an actual
                # p of 0.0003 -- the validator endorsing a false claim of
                # non-significance, which is worse than no check at all.
                op = {"≤": "<=", "≥": ">="}.get(pm.group(1), pm.group(1))
                eps = 1e-12
                cmp = {
                    "<":  lambda pv: pv < value - eps,
                    "<=": lambda pv: pv <= value + eps,
                    ">":  lambda pv: pv > value + eps,
                    ">=": lambda pv: pv >= value - eps,
                    "=":  lambda pv: abs(round(pv, decimals) - value) <= 1e-9,
                }[op]
                claim.kind = f"p_threshold({op})"
                scope = ([bundle.get(t) for t in line_tokens] if line_tokens
                         else list(bundle.records))
                hit = next(
                    (r.token for r in scope if r.p_value is not None and cmp(r.p_value)),
                    None,
                )
                if hit and len(set(line_tokens)) > 1:
                    # Proximity cannot say WHICH estimate this p belongs to when
                    # a line carries several. Loose mode reports rather than
                    # pretends; strict mode with [[R:tok|p]] removes the guess.
                    claim.kind += "-ambiguous"
                    report.ambiguous.append(claim)
                if hit:
                    claim.matched_token, claim.verdict = hit, "MATCHED"
                    report.matched += 1
                else:
                    claim.verdict = "UNMATCHED"
                    report.unmatched.append(claim)
                continue

            # Try the value as written, and across the percent/proportion
            # boundary. Rescaling must carry the precision with it: 41.7 has
            # one decimal, but 0.417 has three, and reusing the written
            # precision after dividing by 100 widens the tolerance a
            # hundredfold -- which once let an invented 41.7 match a genuine
            # 0.4471. Each candidate therefore carries its own decimals.
            exact: list[tuple[float, int]] = [(value, decimals)]
            scaled: list[tuple[float, int]] = [
                (value / 100.0, decimals + 2),
                (value * 100.0, max(decimals - 2, 0)),
            ]
            hit = None
            for v, token, units in pairs:
                if any(_close(t, v, d) for t, d in exact):
                    # Units gate the exact path too, not only the rescaled one.
                    # A bundle value of 2.41 USD validated a claim of 2.41 pp
                    # because only the /100 conversion checked compatibility.
                    if units and unit_hint and not _units_agree(units, unit_hint):
                        continue
                    hit = token
                    break
                # Only cross the percent/proportion boundary when both sides
                # are actually on that scale.
                if (pct or PERCENTISH.search(after[:8]) or PERCENTISH.search(before[-12:])) \
                        and PERCENTISH.search(units or ""):
                    if any(_close(t, v, d) for t, d in scaled):
                        hit = token
                        break
            if hit:
                claim.matched_token, claim.verdict = hit, "MATCHED"
                line_tokens.append(hit)
                report.matched += 1
            else:
                claim.verdict = "UNMATCHED"
                report.unmatched.append(claim)

    return report


def expand_macros(text: str) -> str:
    """`[[P:h1]]` -> `[[R:h1|p]]`, so macros and field tokens are one code path."""
    return MACRO.sub(
        lambda m: f"[[R:{m.group(2)}|{MACRO_FIELD[m.group(1)]}]]" if m.group(1) else m.group(0),
        text)


def resolve_tokens(text: str, bundle: ResultsBundle, decimals: int = 2) -> str:
    """Render `[[R:token]]` citations into formatted numbers.

    Write the token, resolve at build time. The manuscript source then cannot
    disagree with the results, because it never contained the digits.
    """
    def sub(m: re.Match) -> str:
        return bundle.get(m.group(1)).render(m.group(2) or "estimate", decimals)
    # Macros first, or a manuscript written with [[P:h1]] renders with the raw
    # macro still in the prose -- which then reads as a missing number rather
    # than as an unresolved citation.
    return TOKEN_CITATION.sub(sub, expand_macros(text))


def resolve_literals(text: str, literals: dict[str, Any]) -> str:
    """Render `[[L:id]]` citations from the literal registry."""
    def sub(m: re.Match) -> str:
        entry = literals[m.group(1)]
        return str(entry["value"] if isinstance(entry, dict) else entry)
    return LITERAL_CITATION.sub(sub, text)
