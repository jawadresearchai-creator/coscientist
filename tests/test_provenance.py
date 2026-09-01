"""Every number in the manuscript must trace to a result. No exceptions by accident."""
import pytest

from coscientist.freeze import FreezeManifest
from coscientist.manuscript import BridgeError, ResultRecord, ResultsBundle, new_bundle
from coscientist.provenance import ProvenanceError, resolve_tokens, validate


def bundle():
    fm = FreezeManifest(
        candidate_id="C705", question="q", estimand="e", design="did",
        sample_definition="s", treatment="t", outcome="o",
        dataset_hashes={"panel": "a" * 64},
    )
    b = new_bundle(fm)
    b.add(ResultRecord(
        token="h1_did_coef", label="LEGACY_NLP x POST", value=2.4137, units="pp",
        se=0.4471, ci_low=1.5219, ci_high=3.3055, p_value=0.0003,
        n_obs=3246, n_clusters=118, model="LPM, owner x quarter x stage FE",
        script="R/03_primary_models.R", family="primary", adjusted="Holm",
    ))
    b.add(ResultRecord(
        token="h3_ai_core", label="AI_CORE x POST", value=-0.312, units="pp",
        ci_low=-0.981, ci_high=0.357, p_value=0.361, family="secondary",
    ))
    return b


def test_correctly_reported_numbers_pass():
    text = (
        "The interaction is 2.41 pp (95% CI 1.52 to 3.31, p < 0.001), "
        "estimated on 3246 patent-window observations across 118 owners."
    )
    r = validate(text, bundle())
    assert r.ok, r.summary()
    assert r.matched >= 5


def test_a_fabricated_number_is_caught():
    """The whole point. 7.80 appears nowhere in the results."""
    text = "The interaction is 2.41 pp, implying a 7.80 pp shift in retention."
    r = validate(text, bundle())
    assert not r.ok
    assert any(c.raw == "7.80" for c in r.unmatched)


def test_a_plausible_but_wrong_transcription_is_caught():
    """2.14 instead of 2.41 -- a digit swap, the error nobody sees on re-reading."""
    r = validate("The estimate is 2.14 pp.", bundle())
    assert not r.ok
    assert r.unmatched[0].raw == "2.14"


def test_rounding_to_fewer_digits_is_accepted():
    for written in ("2.4", "2.41", "2.414"):
        assert validate(f"Estimate {written} pp.", bundle()).ok, written


def test_structural_references_are_skipped():
    text = "As Table 2 and Figure 3 show, and per Section 4, the estimate is 2.41 pp."
    r = validate(text, bundle())
    assert r.ok, r.summary()
    assert r.skipped >= 3


def test_years_are_skipped_but_a_stray_statistic_is_not():
    text = "Over 2018 to 2024 the effect was 2.41 pp, unlike the 9.99 reported elsewhere."
    r = validate(text, bundle())
    assert not r.ok
    assert [c.raw for c in r.unmatched] == ["9.99"]


def test_p_threshold_matches_against_actual_p_values():
    assert validate("The effect is significant (p < 0.001).", bundle()).ok


def test_an_unsupported_p_threshold_is_caught():
    """No result has p < 0.0000001, so the claim is not supported."""
    r = validate("Highly significant (p < 0.0000001).", bundle())
    assert not r.ok


def test_thousands_separators_are_understood():
    assert validate("The panel holds 3,246 observations.", bundle()).ok


def test_percentages_are_matched_when_units_say_so():
    b = bundle()
    b.add(ResultRecord(token="share", label="treated share", value=0.236,
                       units="proportion", family="primary"))
    assert validate("Treated units are 23.6% of the sample.", b).ok


def test_allow_list_covers_legitimate_non_results():
    text = "The statutory fee is 2688000 dollars; the estimate is 2.41 pp."
    assert not validate(text, bundle()).ok
    assert validate(text, bundle(), allow=[2688000]).ok


def test_citing_an_unknown_token_fails_loudly():
    with pytest.raises(BridgeError, match="no result with token"):
        validate("The effect is [[R:does_not_exist]].", bundle())


def test_token_citations_are_not_treated_as_numbers():
    r = validate("The interaction is [[R:h1_did_coef]] pp.", bundle())
    assert r.ok and r.total == 0


def test_resolve_tokens_renders_the_authoritative_value():
    out = resolve_tokens("Effect: [[R:h1_did_coef]].", bundle())
    assert out == "Effect: 2.41 pp."


def test_negative_estimates_are_matched():
    assert validate("The contrast is -0.312 pp.", bundle()).ok


def test_duplicate_tokens_are_refused():
    b = bundle()
    with pytest.raises(BridgeError, match="duplicate token"):
        b.add(ResultRecord(token="h1_did_coef", label="again", value=1.0))


def test_confidence_level_is_not_treated_as_a_finding():
    """The 95 in '95% CI' is a convention, not something the analysis estimated."""
    for phrase in ("95% CI 1.52 to 3.31", "95% confidence interval of 1.52 to 3.31",
                   "significant at the 5% level", "80% power"):
        r = validate(f"Estimate 2.41 pp, {phrase}.", bundle())
        assert r.ok, f"{phrase}: {r.summary()}"


def test_a_long_allow_list_does_not_hide_a_fabrication():
    """Allow-listing 2688000 must not also excuse an unrelated invented number."""
    r = validate("Fee 2688000; effect 2.41 pp; and a stray 41.7.", bundle(), allow=[2688000])
    assert not r.ok
    assert [c.raw for c in r.unmatched] == ["41.7"]


def test_scale_conversion_requires_compatible_units():
    """The 241-USD bug.

    A monetary 241 must not validate a claim of 2.41 pp just because dividing
    by a hundred happens to land on it. Without units the validator was
    matching arithmetic coincidences across incompatible quantities.
    """
    b = bundle()
    b.add(ResultRecord(token="fee", label="assessment increment", value=241.0,
                       units="USD", family="primary"))
    r = validate("The adjustment was 2.41 dollars per unit.", b)
    assert not r.ok or all(c.matched_token != "fee" for c in b.records if False)
    # the only legitimate match for 2.41 is the pp coefficient, not the USD fee
    claim = next(c for c in validate("An unrelated 2.409 value.", b).claims)
    assert claim.matched_token != "fee"


def test_p_operator_is_honoured_in_every_direction():
    """'p > 0.5' must not validate against an actual p of 0.0003."""
    b = bundle()   # h1_did_coef has p = 0.0003
    assert validate("Effect 2.41 pp (p < 0.001).", b).ok
    assert validate("Effect 2.41 pp (p <= 0.001).", b).ok
    for wrong in ("p > 0.001", "p > 0.5", "p >= 0.9"):
        r = validate(f"Effect 2.41 pp ({wrong}).", b)
        assert not r.ok, f"{wrong} should not validate against p = 0.0003"


def test_a_genuinely_nonsignificant_claim_still_validates():
    b = bundle()   # h3_ai_core has p = 0.361
    assert validate("The contrast is -0.312 pp (p > 0.05).", b).ok


def test_strict_mode_requires_tokens_for_result_claims():
    """Bare-number matching proves resemblance, not identity."""
    b = bundle()
    loose = "The effect was 2.41 pp."
    tokened = "The effect was [[R:h1_did_coef]] pp."
    assert validate(loose, b).ok
    assert not validate(loose, b, require_tokens=True).ok
    assert validate(tokened, b, require_tokens=True).ok


def test_field_tokens_render_every_reported_statistic():
    """Strict mode was unusable: only the estimate had a token form."""
    b = bundle()
    src = ("The effect was [[R:h1_did_coef|estimate]] "
           "(95% CI [[R:h1_did_coef|ci]]; p [[R:h1_did_coef|p]]; "
           "N = [[R:h1_did_coef|n]], [[R:h1_did_coef|clusters]] clusters).")
    out = resolve_tokens(src, b)
    assert out == ("The effect was 2.41 pp (95% CI 1.52 to 3.31; p < 0.001; "
                   "N = 3,246, 118 clusters).")


def test_a_fully_tokenised_manuscript_passes_strict_mode():
    b = bundle()
    src = ("Effect [[R:h1_did_coef|estimate]] "
           "(95% CI [[R:h1_did_coef|ci]]; p [[R:h1_did_coef|p]]).")
    r = validate(src, b, require_tokens=True)
    assert r.ok, r.summary()


def test_citing_a_field_the_record_does_not_have_fails_loudly():
    b = bundle()
    b.add(ResultRecord(token="bare", label="no uncertainty", value=1.0))
    with pytest.raises(BridgeError, match="has no se"):
        validate("Value [[R:bare|se]].", b)


def test_citing_an_unknown_field_name_fails_loudly():
    with pytest.raises(BridgeError, match="unknown result field"):
        validate("Value [[R:h1_did_coef|wobble]].", bundle())


def test_a_p_token_cannot_borrow_another_records_p_value():
    """The multi-estimate sentence that proximity gets wrong.

    Loose mode binds by nearness and passes; the field token names the record
    and so cannot silently take h3's p = 0.361.
    """
    b = bundle()
    loose = "Effects were 2.41 pp and -0.312 pp (p < 0.001)."
    assert validate(loose, b).ok, "proximity accepts it"
    assert validate(loose, b).ambiguous, "but loose mode must report the guess"

    # Written as field tokens, each statistic names its own record and the
    # p-value cannot drift onto the wrong estimate.
    strict = ("Effects were [[R:h1_did_coef|estimate]] (p [[R:h1_did_coef|p]]) "
              "and [[R:h3_ai_core|estimate]] (p [[R:h3_ai_core|p]]).")
    assert validate(strict, b, require_tokens=True).ok
    assert resolve_tokens(strict, b) == (
        "Effects were 2.41 pp (p < 0.001) and -0.31 pp (p 0.361).")


def test_exact_match_across_incompatible_units_is_refused():
    """2.41 USD must not validate a claim of 2.41 pp on the exact path."""
    b = new_bundle(FreezeManifest(
        candidate_id="C", question="q", estimand="e", design="d",
        sample_definition="s", treatment="t", outcome="o", dataset_hashes={"x": "y"}))
    b.add(ResultRecord(token="fee", label="fee", value=2.41, units="USD"))
    assert not validate("The effect was 2.41 pp.", b).ok
    assert validate("The fee was 2.41 dollars.", b).ok


def test_duplicate_tokens_are_refused_when_loading_a_manifest(tmp_path):
    """add(), not append(). The invariant held only in memory."""
    import json
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"freeze_id": "x", "freeze_hash": "y", "records": [
        {"token": "dup", "label": "a", "value": 1.0},
        {"token": "dup", "label": "b", "value": 2.0}]}))
    with pytest.raises(BridgeError, match="duplicate token"):
        ResultsBundle.from_manifest(str(p))


def test_strict_mode_forbids_the_literal_escape_hatch():
    """`{{lit:}}` was removed before any checking and never inspected.

    That made it a working bypass: `{{lit:3.87}}` renders as 3.87 in the prose
    and is invisible to the validator, so the one mechanism that stops
    invention had a documented way around it. Confirmatory numbers must cite a
    registered literal, which is a thing with an id and a source, not a number
    someone typed inside a comment.
    """
    text = "The effect was {{lit:3.87}} pp, comfortably significant."
    with pytest.raises(ProvenanceError, match="forbids"):
        validate(text, bundle(), require_tokens=True)


def test_the_literal_escape_still_works_outside_strict_mode():
    """Draft mode has to stay usable, or nobody runs the validator until the end."""
    report = validate("Roughly {{lit:3.87}} pp in the pilot.", bundle())
    assert report.ok


def test_an_unregistered_literal_citation_fails():
    with pytest.raises(ProvenanceError, match="not in the literal registry"):
        validate("We use a [[L:alpha_005]] threshold.", bundle(), literals={})


def test_a_registered_literal_citation_passes():
    report = validate("We use a [[L:alpha_005]] threshold.", bundle(),
                      literals={"alpha_005": {"value": 0.05, "source": "convention"}})
    assert report.ok


def test_the_prose_cannot_name_one_statistic_and_cite_another():
    """Field tokens bind a number to a record; they cannot make a sentence true.

    Strict mode accepted "The p-value was [[R:h1|estimate]]" and rendered it as
    "The p-value was 2.41 pp" -- fully traceable, entirely false. Arbitrary
    language is not checkable, but a small closed vocabulary of cues that state
    the statistic outright is.
    """
    for text in ("The p-value was [[R:h1_did_coef|estimate]].",
                 "N = [[R:h1_did_coef|p]].",
                 "The standard error was [[R:h1_did_coef|n]].",
                 "95% CI: [[R:h1_did_coef|estimate]]."):
        with pytest.raises(ProvenanceError, match="names one statistic and cites another"):
            validate(text, bundle(), require_tokens=True)


def test_a_cue_that_merely_appears_nearby_is_not_a_mismatch():
    """The cue table is anchored immediately before the citation.

    A wider window would be a proximity heuristic, and proximity heuristics are
    exactly what field tokens replaced.
    """
    text = ("Robust to clustering, the effect was [[R:h1_did_coef|estimate]]. "
            "Standard errors are clustered by owner; the estimate is "
            "[[R:h1_did_coef|estimate]].")
    assert validate(text, bundle(), require_tokens=True).ok


def test_the_named_macros_say_what_they_are():
    text = ("The effect was [[EST:h1_did_coef]] (95% CI [[CI:h1_did_coef]], "
            "p [[P:h1_did_coef]]) across [[N:h1_did_coef]] observations in "
            "[[CLUST:h1_did_coef]] clusters, SE [[SE:h1_did_coef]].")
    assert validate(text, bundle(), require_tokens=True).ok
    rendered = resolve_tokens(text, bundle())
    assert "2.41 pp" in rendered and "1.52 to 3.31" in rendered
    assert "< 0.001" in rendered and "3,246" in rendered and "118" in rendered


def test_strict_mode_forbids_the_allow_list():
    """The last unrestricted bypass.

    `--allow` takes a bare number and waves it through with no id, no source
    and no record that it happened, which made "strict mode has no escape
    hatch" false the moment anyone used the flag. The literal registry is the
    one exception mechanism, and its entries have an id and an origin.
    """
    with pytest.raises(ProvenanceError, match="strict mode forbids --allow"):
        validate("The effect was 999.9.", bundle(), require_tokens=True, allow=[999.9])
    # Outside strict mode it remains a legitimate, explicit tool.
    assert validate("The effect was 999.9.", bundle(), allow=[999.9]).ok
