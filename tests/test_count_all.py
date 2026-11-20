from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def test_count_without_query():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{"a": 1}, {"a": 2}])
    assert db.count() == 2
    assert db.table("empty").count() == 0


def test_count_with_query_unchanged():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{"a": 1}, {"a": 2}, {"a": 1}])
    assert db.count(where("a") == 1) == 2
