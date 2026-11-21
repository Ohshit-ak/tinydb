from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage
from tinydb.operations import append, increment


def test_ac1_append_to_existing_list_field():
    """
    AC-1: Append to existing list field (verifies FR-001)
    Then db.get(where('name') == 'post-1')['tags'] returns ['python', 'db']
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert({'name': 'post-1', 'tags': ['python']})

    db.update(append('tags', 'db'), where('name') == 'post-1')

    assert db.get(where('name') == 'post-1')['tags'] == ['python', 'db']


def test_ac2_append_to_missing_field_creates_new_list():
    """
    AC-2: Append to missing field creates new list (verifies FR-001)
    Then db.get(where('name') == 'post-2')['tags'] returns ['python']
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert({'name': 'post-2'})

    db.update(append('tags', 'python'), where('name') == 'post-2')

    assert db.get(where('name') == 'post-2')['tags'] == ['python']


def test_ac3_existing_operations_remain_unaffected():
    """
    AC-3: Existing operations remain unaffected (verifies FR-002)
    Then db.get(where('name') == 'post-1')['int'] returns 2
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert({'name': 'post-1', 'int': 1, 'char': 'a'})

    db.update(increment('int'), where('name') == 'post-1')

    assert db.get(where('name') == 'post-1')['int'] == 2
