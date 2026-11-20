from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def test_held_table_after_drop_table():
    db = TinyDB(storage=MemoryStorage)
    t = db.table("t")
    t.insert({"x": 1})
    assert t.search(where("x") == 1) == [{"x": 1}]
    db.drop_table("t")
    assert t.search(where("x") == 1) == []
    assert t.insert({"x": 2}) == 1


def test_held_table_after_drop_tables():
    db = TinyDB(storage=MemoryStorage)
    t = db.table("t")
    t.insert({"x": 1})
    t.search(where("x") == 1)
    db.drop_tables()
    assert t.count(where("x") == 1) == 0
