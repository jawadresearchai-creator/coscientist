from io import StringIO

from coscientist.eventstudy_io import read_csv_preserve_literals


def test_literal_na_ticker_is_not_parsed_as_missing():
    df = read_csv_preserve_literals(StringIO("ticker,cik\nNA,0001872302\nAAPL,0000320193\n"), dtype=str)
    assert df.loc[0, "ticker"] == "NA"
    assert df.loc[0, "cik"] == "0001872302"
    assert df["ticker"].isna().sum() == 0
