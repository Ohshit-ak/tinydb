import pytest
from tinydb import TinyDB
from tinydb.storages import MemoryStorage


def test_failed_batch_does_not_skip_ids():
    db = TinyDB(storage=MemoryStorage)
    db.insert({"t": 1})
    with pytest.raises(ValueError):
        db.insert_multiple([{"t": 2}, "bad"])
    assert db.insert({"t": 3}) == 2
