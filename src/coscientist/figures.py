"""Figures as provenanced objects, not decorations.

A figure is a claim. "Effects emerge in quarter three and persist" is asserted
by a picture exactly as much as by a sentence, and a picture asserting an
effect the results do not contain is fabrication that no numeric validator can
see -- the provenance check reads prose, and a figure has none.

So every figure declares which results it visualises, and those tokens must
resolve in the bundle. A figure naming no result is refused rather than merely
noted: an unlabelled picture in a submitted paper is where an unprovenanced
claim would live.

The manifest also carries the file's own hash, so `FIGURE_MANIFEST.md` names
bytes rather than intentions, and a figure regenerated after the results were
frozen is visible as a changed hash.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, asdict
from typing import Any

VECTOR = (".pdf", ".eps", ".svg")
RASTER = (".png", ".tif", ".tiff", ".jpg", ".jpeg")

# Most economics and management journals specify 300 dpi for raster artwork and
# prefer vector. A figure that fails this is not wrong, but it is a desk-reject
# risk discovered at submission rather than at build time.
MIN_RASTER_DPI = 300


class FigureError(RuntimeError):
    pass


@dataclass
class FigureRecord:
    id: str
    path: str
    caption: str
    script: str = ""
    result_tokens: list[str] = field(default_factory=list)
    dpi: int = 0
    width_in: float | None = None
    height_in: float | None = None
    panels: int = 1
    sha256: str = ""
    bytes: int = 0

    @property
    def fmt(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    @property
    def is_vector(self) -> bool:
        return self.fmt in VECTOR

    def publication_warnings(self) -> list[str]:
        out: list[str] = []
        if self.fmt not in VECTOR + RASTER:
            out.append(f"{self.id}: unrecognised figure format {self.fmt or '(none)'}")
        if self.fmt in RASTER and self.dpi and self.dpi < MIN_RASTER_DPI:
            out.append(
                f"{self.id}: {self.dpi} dpi raster, below the {MIN_RASTER_DPI} dpi "
                "most journals require; regenerate as vector or at higher density"
            )
        if self.fmt in RASTER and not self.dpi:
            out.append(f"{self.id}: raster figure with no declared dpi")
        if not self.caption.strip():
            out.append(f"{self.id}: no caption")
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["format"] = self.fmt
        return d


def hash_file(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
            total += len(chunk)
    return h.hexdigest(), total


def bind(figures: list[FigureRecord], known_tokens: set[str], *,
         base_dir: str = ".") -> list[FigureRecord]:
    """Hash every figure file and bind it to the results it depicts.

    Refuses, rather than warns, on three things: a figure whose file is absent,
    a figure citing a result that does not exist, and a figure citing nothing at
    all. The third is the one worth arguing about -- and it is the one that
    matters, because a picture with no declared result is the only place in this
    pipeline where a number can appear that no validator ever reads.
    """
    seen: dict[str, str] = {}
    out: list[FigureRecord] = []
    problems: list[str] = []

    for fig in figures:
        if fig.id in seen:
            problems.append(f"duplicate figure id {fig.id!r} (also from {seen[fig.id]})")
            continue
        seen[fig.id] = fig.script

        full = fig.path if os.path.isabs(fig.path) else os.path.join(base_dir, fig.path)
        if not os.path.exists(full):
            problems.append(
                f"{fig.id}: no file at {fig.path}. The script declared a figure it "
                "did not write, or wrote it somewhere else."
            )
            continue
        fig.sha256, fig.bytes = hash_file(full)

        if not fig.result_tokens:
            problems.append(
                f"{fig.id}: declares no result tokens. A figure asserts a finding as "
                "surely as a sentence does, and a figure bound to nothing is the one "
                "claim in this pipeline that no validator can check."
            )
            continue
        unknown = [t for t in fig.result_tokens if t not in known_tokens]
        if unknown:
            problems.append(
                f"{fig.id}: plots result(s) not in the bundle: {', '.join(unknown)}"
            )
            continue
        out.append(fig)

    if problems:
        raise FigureError("figure manifest is not sound:\n  - " + "\n  - ".join(problems))
    return out


def write_manifest(path: str, figures: list[FigureRecord], freeze_id: str,
                   results_hash: str) -> str:
    lines = [
        "# Figure manifest",
        "",
        f"Freeze: `{freeze_id}`  ",
        f"Results hash: `{results_hash[:16]}`  ",
        f"{len(figures)} figure(s)",
        "",
        "Every figure names the results it depicts and the bytes it is. A caption "
        "may be rewritten; the binding below may not.",
        "",
    ]
    warnings: list[str] = []
    for f in sorted(figures, key=lambda x: x.id):
        lines += [
            f"## {f.id}",
            "",
            f"- File: `{f.path}` ({f.fmt.lstrip('.') or '?'}, {f.bytes:,} bytes)",
            f"- SHA-256: `{f.sha256[:32]}`",
            f"- Caption: {f.caption}",
            f"- Depicts: {', '.join('`' + t + '`' for t in f.result_tokens)}",
            f"- Produced by: `{f.script}`" if f.script else "",
        ]
        if f.dpi:
            dims = (f"{f.width_in}x{f.height_in} in" if f.width_in and f.height_in else "")
            lines.append(f"- Rendering: {f.dpi} dpi{', ' + dims if dims else ''}"
                         f"{f', {f.panels} panels' if f.panels > 1 else ''}")
        lines.append("")
        warnings += f.publication_warnings()

    if warnings:
        lines += ["## Submission warnings", ""]
        lines += [f"- {w}" for w in warnings]
        lines.append("")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
