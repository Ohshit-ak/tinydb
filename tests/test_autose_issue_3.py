import pytest
from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage


def test_ac1_table_count_without_argument():
    """
    AC-1: Table count without argument verifies FR-001
    Given a db = TinyDB(storage=MemoryStorage) and table table = db.table('notes') containing 3 documents inserted via table.insert({'a': 1})
    When table.count() is called with no arguments
    Then it returns 3 and does not raise a TypeError
    """
    db = TinyDB(storage=MemoryStorage)
    table = db.table('notes')
    table.insert({'a': 1})
    table.insert({'a': 1})
    table.insert({'a': 1})

    assert table.count() == 3


def test_ac2_tinydb_count_without_argument():
    """
    AC-2: TinyDB count without argument verifies FR-001
    Given a db = TinyDB(storage=MemoryStorage) containing 2 documents in the default table inserted via db.insert({'b': 2})
    When db.count() is called with no arguments
    Then it returns 2 and does not raise a TypeError
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert({'b': 2})
    db.insert({'b': 2})

    assert db.count() == 2


def test_ac3_table_count_with_query_preserves_existing_behaviour():
    """
    AC-3: Table count with query preserves existing behaviour verifies FR-002
    Given a db = TinyDB(storage=MemoryStorage) and table table = db.table('notes') containing documents {'type': 'a'} (twice) and {'type': 'b'} (once), with Query = Query()
    When table.count(Query.type == 'a') is called
    Then it returns 2
    """
    db = TinyDB(storage=MemoryStorage)
    table = db.table('notes')
    table.insert({'type': 'a'})
    table.insert({'type': 'a'})
    table.insert({'type': 'b'})

    Q = Query()
    assert table.count(Q.type == 'a') == 2


def test_ac4_unchanged_len_behaviour_remains_the_same():
    """
    AC-4: Unchanged len behaviour remains the same
    Given a db = TinyDB(storage=MemoryStorage) and table table = db.table('notes') containing 4 documents
    When len(table) and len(db) are called
    Then both return 4 exactly as before
    """
    db = TinyDB(storage=MemoryStorage)
    table = db.table('notes')
    for i in range(4):
        table.insert({'val': i})

    assert len(table) == 4

    db_default = TinyDB(storage=MemoryStorage)
    for i in range(4):
        db_default.insert({'val': i})

    assert len(db_default) == 4
