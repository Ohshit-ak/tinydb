import pytest
from tinydb import TinyDB
from tinydb.storages import MemoryStorage
from tinydb.table import Document


def test_ac1_next_id_preserved_after_insert_multiple_invalid_type():
    """
    AC-1: Next ID is preserved after `insert_multiple` failure due to invalid document type
    - Given a `TinyDB` instance with storage `MemoryStorage` containing one document with ID `1` (`db.insert({'t': 'first'})` -> `1`),
    - When `db.insert_multiple([{'t': 'ok'}, 'oops not a dict', {'t': 'ok2'}])` is executed and catches `ValueError`,
    - Then a subsequent call to `db.insert({'t': 'second'})` returns `2`.
    """
    db = TinyDB(storage=MemoryStorage)
    assert db.insert({'t': 'first'}) == 1

    with pytest.raises(ValueError):
        db.insert_multiple([{'t': 'ok'}, 'oops not a dict', {'t': 'ok2'}])

    assert db.insert({'t': 'second'}) == 2


def test_ac2_next_id_preserved_after_insert_multiple_collision():
    """
    AC-2: Next ID is preserved after `insert_multiple` failure due to existing document ID collision
    - Given a `TinyDB` instance with storage `MemoryStorage` containing a document with ID `1` (`db.insert({'t': 'first'})` -> `1`),
    - When `db.insert_multiple([Document({'t': 'ok'}, 1)])` is executed and catches `ValueError` (due to ID `1` already existing),
    - Then a subsequent call to `db.insert({'t': 'second'})` returns `2`.
    """
    db = TinyDB(storage=MemoryStorage)
    assert db.insert({'t': 'first'}) == 1

    with pytest.raises(ValueError):
        db.insert_multiple([Document({'t': 'ok'}, 1)])

    assert db.insert({'t': 'second'}) == 2


def test_ac3_successful_insert_multiple_behavior():
    """
    AC-3: Successful `insert_multiple` behavior remains unaffected
    - Given an empty `TinyDB` instance with storage `MemoryStorage`,
    - When `db.insert_multiple([{'t': 'a'}, {'t': 'b'}])` is executed successfully,
    - Then the returned document IDs are `[1, 2]` and a subsequent call to `db.insert({'t': 'c'})` returns `3`.
    """
    db = TinyDB(storage=MemoryStorage)
    assert db.insert_multiple([{'t': 'a'}, {'t': 'b'}]) == [1, 2]
    assert db.insert({'t': 'c'}) == 3
