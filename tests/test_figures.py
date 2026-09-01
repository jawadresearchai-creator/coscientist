"""A figure is a claim, and the only one no numeric validator reads."""
import os

import pytest

from coscientist.figures import FigureError, FigureRecord, bind, write_manifest


def fig(tmp_path, **over):
    p = tmp_path / "fig1.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    kw = dict(id="Figure_1", path="fig1.pdf", caption="Event-study coefficients",
              script="R/05_figures.R", result_tokens=["h1"], dpi=600,
              width_in=6.5, height_in=4.0)
    kw.update(over)
    return FigureRecord(**kw)


def test_a_bound_figure_is_hashed(tmp_path):
    out = bind([fig(tmp_path)], {"h1"}, base_dir=str(tmp_path))
    assert len(out[0].sha256) == 64 and out[0].bytes == 13


def test_a_figure_bound_to_nothing_is_refused(tmp_path):
    """The one place an unprovenanced claim could live.

    "Effects emerge in quarter three and persist" is asserted by a picture
    exactly as much as by a sentence. The provenance validator reads prose; a
    figure has none, so the binding has to be declared or the claim escapes
    every check in the system.
    """
    with pytest.raises(FigureError, match="no result tokens"):
        bind([fig(tmp_path, result_tokens=[])], {"h1"}, base_dir=str(tmp_path))


def test_a_figure_plotting_a_result_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(FigureError, match="not in the bundle"):
        bind([fig(tmp_path, result_tokens=["h1", "ghost"])], {"h1"}, base_dir=str(tmp_path))


def test_a_declared_figure_with_no_file_is_refused(tmp_path):
    with pytest.raises(FigureError, match="no file at"):
        bind([fig(tmp_path, path="never_written.pdf")], {"h1"}, base_dir=str(tmp_path))


def test_duplicate_figure_ids_are_refused(tmp_path):
    with pytest.raises(FigureError, match="duplicate figure id"):
        bind([fig(tmp_path), fig(tmp_path)], {"h1"}, base_dir=str(tmp_path))


def test_every_problem_is_reported_at_once(tmp_path):
    """One run, every fault. Fixing figures one round trip at a time is how a
    submission deadline gets eaten by a manifest."""
    bad = [fig(tmp_path, id="A", path="gone.pdf"),
           fig(tmp_path, id="B", result_tokens=["ghost"])]
    with pytest.raises(FigureError) as exc:
        bind(bad, {"h1"}, base_dir=str(tmp_path))
    assert "A:" in str(exc.value) and "B:" in str(exc.value)


def test_a_low_resolution_raster_is_flagged_before_submission(tmp_path):
    p = tmp_path / "fig1.png"
    p.write_bytes(b"\x89PNG stub")
    f = fig(tmp_path, path="fig1.png", dpi=96)
    assert any("below the 300 dpi" in w for w in f.publication_warnings())
    assert fig(tmp_path).publication_warnings() == []   # vector is exempt


def test_the_manifest_names_bytes_not_intentions(tmp_path):
    figs = bind([fig(tmp_path)], {"h1"}, base_dir=str(tmp_path))
    path = str(tmp_path / "FIGURE_MANIFEST.md")
    write_manifest(path, figs, "DF-C705-abc", "d" * 64)
    text = open(path).read()
    assert figs[0].sha256[:32] in text
    assert "`h1`" in text and "DF-C705-abc" in text
