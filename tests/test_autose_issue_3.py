from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def test_ac1_count_without_condition():
    """
    AC-1: Calling db.count() without arguments returns total document count (verifies FR-001)
    Then count returns 3
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{'int': 1}, {'int': 2}, {'int': 3}])
    count = db.count()
    assert count == 3


def test_ac2_count_with_query():
    """
    AC-2: Calling db.count(query) with a query condition works as before (verifies FR-002)
    Then count returns 2
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple([{'int': 1}, {'int': 1}, {'int': 2}])
    count = db.count(where('int') == 1)
    assert count == 2


def test_ac3_table_count_without_condition():
    """
    AC-3: Calling table.count() without arguments returns total table document count (verifies FR-001)
    Then count returns 2
    """
    db = TinyDB(storage=MemoryStorage)
    table = db.table('custom')
    table.insert_multiple([{'char': 'a'}, {'char': 'b'}])
    count = table.count()
    assert count == 2


def test_ac4_table_count_with_query():
    """
    AC-4: Existing query-based counting behaviour for tables remains unchanged (verifies FR-002)
    Then count returns 1
    """
    db = TinyDB(storage=MemoryStorage)
    table = db.table('custom')
    table.insert_multiple([{'char': 'a'}, {'char': 'b'}])
    count = table.count(where('char') == 'a')
    assert count == 1
