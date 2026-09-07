from coscientist.astra_exposure import mention_count, stats, standardized, term_counts


def test_overlapping_generative_ai_is_counted_once():
    text = "We deploy generative AI systems across the business."
    assert mention_count(text) == 1
    counts = term_counts(text)
    assert counts["generative_ai"] == 1
    assert counts["ai"] == 0


def test_standalone_ai_is_case_sensitive_but_phrase_is_not():
    assert mention_count("AI improves our products.") == 1
    assert mention_count("ai is also an ordinary token here.") == 0
    assert mention_count("Artificial Intelligence improves our products.") == 1


def test_frontier_subset_is_separate_from_core_measure():
    text = "We use machine learning and large language models from OpenAI."
    assert mention_count(text) == 2
    assert mention_count(text, frontier=True) == 2  # LLM phrase + OpenAI


def test_rate_normalizes_by_word_count():
    s = stats("AI " + "word " * 9999)
    assert s.words == 10000
    assert s.mentions == 1
    assert abs(s.per_10k_words - 1.0) < 1e-12
    assert s.any_mention is True


def test_standardized_has_zero_mean_and_unit_population_variance():
    z = standardized([0.0, 1.0, 2.0, 3.0])
    mean = sum(z) / len(z)
    var = sum((x - mean) ** 2 for x in z) / len(z)
    assert abs(mean) < 1e-12
    assert abs(var - 1.0) < 1e-12
