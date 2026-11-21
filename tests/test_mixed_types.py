from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage


def test_mixed_types_do_not_crash():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{"age": 30}, {"age": "thirty"}])
    db.search(Query().age > 18)  # used to raise TypeError
