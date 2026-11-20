from tinydb import TinyDB, where
from tinydb.operations import append
from tinydb.storages import MemoryStorage


def test_append_creates_list():
    db = TinyDB(storage=MemoryStorage)
    db.insert({"name": "p"})
    db.update(append("tags", "x"), where("name") == "p")
    assert db.get(where("name") == "p")["tags"] == ["x"]
