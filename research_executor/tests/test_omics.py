import unittest

from research_executor.omics import (
    geo_supplementary_urls,
    nfcore_fetchngs_command,
    pysradb_command,
    reconcile_ena_rows,
    sra_toolkit_commands,
)


class OmicsTests(unittest.TestCase):
    def test_reconcile_paired_fastq_metadata(self):
        rows = [{
            "run_accession": "SRR1",
            "study_accession": "SRP1",
            "experiment_accession": "SRX1",
            "sample_accession": "SRS1",
            "secondary_sample_accession": "SAMN1",
            "scientific_name": "Example species",
            "library_strategy": "RNA-Seq",
            "library_layout": "PAIRED",
            "instrument_platform": "ILLUMINA",
            "read_count": "10",
            "base_count": "1000",
            "fastq_ftp": "ftp.sra.ebi.ac.uk/a_1.fastq.gz;ftp.sra.ebi.ac.uk/a_2.fastq.gz",
            "fastq_md5": "aaa;bbb",
            "fastq_bytes": "100;200",
        }]
        result = reconcile_ena_rows(rows)
        self.assertEqual(result["run_count"], 1)
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["total_fastq_bytes"], 300)
        self.assertTrue(result["runs"][0]["files"][0]["url"].startswith("https://"))

    def test_pysradb_routes(self):
        self.assertEqual(pysradb_command("GSE123"), ["pysradb", "gse-to-srp", "GSE123"])
        self.assertEqual(pysradb_command("SRP123"), ["pysradb", "srp-to-srr", "SRP123"])

    def test_nfcore_fetchngs_route(self):
        cmd = nfcore_fetchngs_command("ids.csv", "out")
        self.assertIn("nf-core/fetchngs", cmd)
        self.assertIn("1.12.0", cmd)
        self.assertIn("docker", cmd)

    def test_sra_toolkit_fallback(self):
        cmds = sra_toolkit_commands("SRR123", "out")
        self.assertEqual(cmds[0][0], "prefetch")
        self.assertEqual(cmds[1][0], "fasterq-dump")

    def test_geo_supplementary_parser(self):
        text = "!Series_supplementary_file = https://ftp.ncbi.nlm.nih.gov/a.txt\n!Series_title = x\n"
        self.assertEqual(geo_supplementary_urls(text), ["https://ftp.ncbi.nlm.nih.gov/a.txt"])


if __name__ == "__main__":
    unittest.main()
