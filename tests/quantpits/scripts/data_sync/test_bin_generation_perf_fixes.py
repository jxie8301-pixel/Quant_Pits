import pandas as pd

from quantpits.scripts.data_sync.bin_writer import _find_start_index
from quantpits.scripts.data_sync import stock_bin_generator as sbg


def test_find_start_index_uses_calendar_index_map():
    calendar = ["20240101", "20240102", "20240103"]
    calendar_index_map = {"20240101": 0, "20240102": 1, "20240103": 2}

    assert _find_start_index("20240102", calendar, calendar_index_map) == 1
    assert _find_start_index("20240104", calendar, calendar_index_map) == -1


def test_load_raw_events_respects_date_range(monkeypatch):
    monkeypatch.setattr(
        sbg.storage,
        "get_all_synced_dates",
        lambda interface, raw_dir: ["20240101", "20240102", "20240103"],
    )

    exists_set = {
        "/tmp/raw/mock_event/20240101.parquet",
        "/tmp/raw/mock_event/20240102.parquet",
        "/tmp/raw/mock_event/20240103.parquet",
    }
    monkeypatch.setattr(sbg.os.path, "exists", lambda p: p in exists_set)

    class _DummyTable:
        def __init__(self, date_str: str):
            self._date = date_str

        def to_pandas(self):
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "ann_date": [self._date],
                    "v": [1.0],
                }
            )

    def _fake_read_table(path: str):
        date_str = path.split("/")[-1].split(".")[0]
        return _DummyTable(date_str)

    monkeypatch.setattr(sbg.pq, "read_table", _fake_read_table)

    df = sbg._load_raw_events(
        "mock_event",
        "/tmp/raw",
        start_date="20240102",
        end_date="20240102",
    )

    assert not df.empty
    assert set(df["ann_date"].astype(str).tolist()) == {"20240102"}
