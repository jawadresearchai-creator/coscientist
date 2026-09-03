import copy

import pytest

from coscientist.omics import (
    DataRepresentation,
    OmicsCapabilityBlocked,
    OmicsFitnessClass,
    OmicsModality,
    OmicsTargetProfile,
    PublicOmicsDataset,
    assess_omics_fitness,
    choose_representation,
    freeze_omics_datasets,
    verify_omics_freeze,
)


def wheat_root_dataset(**overrides):
    data = dict(
        accession="GSE-TEST-001",
        repository="NCBI_GEO",
        modality=OmicsModality.RNA_SEQ,
        species="Triticum aestivum",
        tissue="root",
        condition="soil compaction mechanical stress",
        biological_replicates=4,
        public=True,
        provenance_url="https://example.org/GSE-TEST-001",
        developmental_stage="seedling",
        sampling_time_hours=24,
        metadata_complete=True,
        raw_available=True,
        counts_available=True,
        processed_available=True,
    )
    data.update(overrides)
    return PublicOmicsDataset(**data)


def target():
    return OmicsTargetProfile(
        species="Triticum aestivum",
        tissue="root",
        condition_terms=("soil compaction", "mechanical stress"),
        mechanism_terms=("mechanical stress", "oxidative stress"),
        developmental_stage="seedling",
        sampling_time_hours=24,
        min_replicates=3,
    )


def test_directly_comparable_dataset_is_class_a():
    result = assess_omics_fitness(wheat_root_dataset(), target())
    assert result.classification is OmicsFitnessClass.A
    assert result.evidence_use == "CONVERGENT"
    assert result.score >= 80


def test_same_species_related_root_condition_is_not_called_direct_validation():
    dataset = wheat_root_dataset(
        condition="root oxidative stress hydrogen peroxide",
        sampling_time_hours=6,
    )
    result = assess_omics_fitness(dataset, target())
    assert result.classification in {OmicsFitnessClass.C, OmicsFitnessClass.B}
    assert result.classification is not OmicsFitnessClass.A


def test_unrelated_cross_species_context_is_contextual_or_rejected():
    dataset = wheat_root_dataset(
        species="Arabidopsis thaliana",
        tissue="leaf",
        condition="drought response",
        developmental_stage="rosette",
    )
    result = assess_omics_fitness(dataset, target())
    assert result.classification in {OmicsFitnessClass.D, OmicsFitnessClass.E}


def test_missing_public_provenance_fails_closed():
    dataset = wheat_root_dataset(public=False, provenance_url=None, source_publication=None)
    result = assess_omics_fitness(dataset, target())
    assert result.classification is OmicsFitnessClass.E
    assert result.hard_failures


def test_counts_are_preferred_when_read_level_reprocessing_is_not_needed():
    assert choose_representation(wheat_root_dataset(), ["differential_expression", "pathway_analysis"]) is DataRepresentation.RAW_COUNTS


def test_fastq_is_required_for_read_level_question():
    assert choose_representation(wheat_root_dataset(), ["alternative_splicing"]) is DataRepresentation.FASTQ


def test_missing_fastq_blocks_read_level_question():
    with pytest.raises(OmicsCapabilityBlocked):
        choose_representation(wheat_root_dataset(raw_available=False), ["alternative_splicing"])


def test_dataset_freeze_is_order_independent_and_tamper_evident():
    a = wheat_root_dataset(accession="GSE-A")
    b = wheat_root_dataset(accession="GSE-B", condition="mechanical stress root impedance")
    f1 = freeze_omics_datasets([a, b], selection_protocol="Include all eligible wheat root mechanical-stress RNA-seq studies.")
    f2 = freeze_omics_datasets([b, a], selection_protocol="Include all eligible wheat root mechanical-stress RNA-seq studies.")
    assert f1.freeze_hash == f2.freeze_hash
    payload = f1.to_dict()
    assert verify_omics_freeze(payload)
    tampered = copy.deepcopy(payload)
    tampered["datasets"][0]["condition"] = "different treatment"
    assert not verify_omics_freeze(tampered)


def test_empty_dataset_freeze_is_refused():
    with pytest.raises(OmicsCapabilityBlocked):
        freeze_omics_datasets([], selection_protocol="protocol")
