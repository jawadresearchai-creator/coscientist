from io import StringIO

import pandas as pd

from coscientist.eventstudy_io import read_csv_preserve_literals


def test_literal_na_ticker_is_not_parsed_as_missing():
    df = read_csv_preserve_literals(StringIO("ticker,cik\nNA,0001872302\nAAPL,0000320193\n"), dtype=str)
    assert df.loc[0, "ticker"] == "NA"
    assert df.loc[0, "cik"] == "0001872302"
    assert df["ticker"].isna().sum() == 0


def test_literal_reader_does_not_recurse_when_pandas_read_csv_is_monkey_patched():
    original = pd.read_csv
    pd.read_csv = read_csv_preserve_literals
    try:
        df = pd.read_csv(StringIO("ticker,cik\nNA,0001872302\n"), dtype=str)
    finally:
        pd.read_csv = original
    assert df.loc[0, "ticker"] == "NA"
    assert df.loc[0, "cik"] == "0001872302"
