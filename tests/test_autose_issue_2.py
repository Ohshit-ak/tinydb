import pytest
from tinydb import TinyDB
from tinydb.table import Document
from tinydb.storages import MemoryStorage


def test_ac1_insert_multiple_invalid_mapping_type():
    """
    AC-1: Subsequent insert ID follows immediately after successful insert after failed insert_multiple due to invalid mapping type
    - Given db = TinyDB(storage=MemoryStorage)
    - When db.insert({'t': 'first'}) is executed, followed by a failed db.insert_multiple([{'t': 'ok'}, 'oops not a dict', {'t': 'ok2'}]) catching ValueError, and then db.insert({'t': 'second'})
    - Then db.insert({'t': 'second'}) returns 2 (integer 2), and table contents contain only documents with ID 1 and ID 2.
    """
    db = TinyDB(storage=MemoryStorage)
    first_id = db.insert({'t': 'first'})
    assert first_id == 1

    with pytest.raises(ValueError):
        db.insert_multiple([{'t': 'ok'}, 'oops not a dict', {'t': 'ok2'}])

    second_id = db.insert({'t': 'second'})
    assert second_id == 2
    assert type(second_id) is int

    all_docs = db.all()
    assert len(all_docs) == 2
    assert [doc.doc_id for doc in all_docs] == [1, 2]
    assert [doc['t'] for doc in all_docs] == ['first', 'second']


def test_ac2_insert_multiple_duplicate_document_id():
    """
    AC-2: Subsequent insert ID follows immediately after successful insert after failed insert_multiple due to duplicate document ID
    - Given db = TinyDB(storage=MemoryStorage)
    - When db.insert({'t': 'first'}) (assigned ID 1) is executed, followed by a failed db.insert_multiple([Document({'t': 'conflict'}, doc_id=1)]) catching ValueError, and then db.insert({'t': 'second'})
    - Then db.insert({'t': 'second'}) returns 2 (integer 2), and table contents contain only documents with ID 1 and ID 2.
    """
    db = TinyDB(storage=MemoryStorage)
    first_id = db.insert({'t': 'first'})
    assert first_id == 1

    with pytest.raises(ValueError):
        db.insert_multiple([Document({'t': 'conflict'}, doc_id=1)])

    second_id = db.insert({'t': 'second'})
    assert second_id == 2
    assert type(second_id) is int

    all_docs = db.all()
    assert len(all_docs) == 2
    assert [doc.doc_id for doc in all_docs] == [1, 2]
    assert [doc['t'] for doc in all_docs] == ['first', 'second']


def test_ac3_successful_insert_multiple_sequential_ids():
    """
    AC-3: Successful insert_multiple assigns sequential IDs
    - Given db = TinyDB(storage=MemoryStorage)
    - When db.insert({'t': 'first'}) is executed, followed by db.insert_multiple([{'t': 'second'}, {'t': 'third'}]), and then db.insert({'t': 'fourth'})
    - Then insert_multiple returns [2, 3], db.insert({'t': 'fourth'}) returns 4, and all four documents are stored with contiguous IDs 1, 2, 3, 4.
    """
    db = TinyDB(storage=MemoryStorage)
    first_id = db.insert({'t': 'first'})
    assert first_id == 1

    multiple_ids = db.insert_multiple([{'t': 'second'}, {'t': 'third'}])
    assert multiple_ids == [2, 3]

    fourth_id = db.insert({'t': 'fourth'})
    assert fourth_id == 4

    all_docs = db.all()
    assert len(all_docs) == 4
    assert [doc.doc_id for doc in all_docs] == [1, 2, 3, 4]
    assert [doc['t'] for doc in all_docs] == ['first', 'second', 'third', 'fourth']
