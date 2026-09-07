import csv
import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from research_executor.factorial_rnaseq import detect_columns, parse_sample_file, safe_extract_csv_gz


class FactorialRNASeqTests(unittest.TestCase):
    def test_detect_raw_count_not_cpm(self):
        gene, count = detect_columns(["gene_id", "raw read count", "CPM"])
        self.assertEqual(gene, "gene_id")
        self.assertEqual(count, "raw read count")

    def test_parse_sample_file_with_geo_style_name(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "GSM4976350_Col0_W_W_rep1_RNA-seq.csv.gz"
            with gzip.open(p, "wt", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["gene", "raw count", "CPM"])
                w.writerow(["AT1G01010", 12, 1.2])
            counts, meta = parse_sample_file(p)
            self.assertEqual(counts["AT1G01010"], 12)
            self.assertEqual(meta["group"], "W_W")
            self.assertEqual(meta["sample_id"], "GSM4976350")

    def test_safe_extract_requires_16(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tar = td / "x.tar"
            content = gzip.compress(b"gene,raw count,CPM\nAT1G01010,1,1.0\n")
            with tarfile.open(tar, "w") as tf:
                for i in range(16):
                    group = ["W_W", "W_JA", "JA_W", "JA_JA"][i // 4]
                    rep = (i % 4) + 1
                    name = f"GSM{4976350+i}_Col0_{group}_rep{rep}_RNA-seq.csv.gz"
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    tf.addfile(info, io.BytesIO(content))
            paths = safe_extract_csv_gz(tar, td / "out")
            self.assertEqual(len(paths), 16)


if __name__ == "__main__":
    unittest.main()
