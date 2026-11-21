from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def test_ac1_stale_query_cache_cleared_after_drop_table():
    """
    AC-1: Stale query cache cleared after drop_table
    - Given a database db = TinyDB(storage=MemoryStorage), a table reference users = db.table('users'), and inserted documents where users.search(where('name') == 'ann') returns [{'name': 'ann'}],
    - When db.drop_table('users') is called,
    - Then users.search(where('name') == 'ann') returns [] and users.count(where('name') == 'ann') returns 0.
    """
    db = TinyDB(storage=MemoryStorage)
    users = db.table('users')
    users.insert({'name': 'ann'})
    
    assert users.search(where('name') == 'ann') == [{'name': 'ann'}]
    
    db.drop_table('users')
    
    assert users.search(where('name') == 'ann') == []
    assert users.count(where('name') == 'ann') == 0


def test_ac2_document_id_sequence_resets_after_drop_table():
    """
    AC-2: Document ID sequence resets after drop_table
    - Given a database db = TinyDB(storage=MemoryStorage), a table reference users = db.table('users'), and inserted documents such that the next ID would be greater than 1,
    - When db.drop_table('users') is called and a new document is inserted via users.insert({'name': 'cat'}),
    - Then the returned document ID is 1.
    """
    db = TinyDB(storage=MemoryStorage)
    users = db.table('users')
    users.insert({'name': 'ann'})
    users.insert({'name': 'bob'})
    
    db.drop_table('users')
    
    doc_id = users.insert({'name': 'cat'})
    assert doc_id == 1


def test_ac3_stale_query_cache_and_id_sequence_cleared_after_drop_tables():
    """
    AC-3: Stale query cache and ID sequence cleared after drop_tables
    - Given a database db = TinyDB(storage=MemoryStorage), a table reference users = db.table('users'), and inserted documents,
    - When db.drop_tables() is called and a new document is inserted via users.insert({'name': 'cat'}),
    - Then users.all() returns [], users.search(where('name') == 'ann') returns [], users.count(where('name') == 'ann') returns 0, and the inserted document ID is 1.
    """
    db = TinyDB(storage=MemoryStorage)
    users = db.table('users')
    users.insert({'name': 'ann'})
    
    assert users.search(where('name') == 'ann') == [{'name': 'ann'}]
    
    db.drop_tables()
    
    assert users.all() == []
    assert users.search(where('name') == 'ann') == []
    assert users.count(where('name') == 'ann') == 0
    
    doc_id = users.insert({'name': 'cat'})
    assert doc_id == 1


def test_ac4_normal_table_operations_without_drop_remain_fully_functional():
    """
    AC-4: Normal table operations without drop remain fully functional
    - Given a database db = TinyDB(storage=MemoryStorage) and a table users = db.table('users'),
    - When users.insert({'name': 'ann'}) is called, followed by users.search(where('name') == 'ann'), users.count(where('name') == 'ann'), and users.all(), without calling drop_table() or drop_tables(),
    - Then users.search(where('name') == 'ann') returns [{'name': 'ann'}], users.count(where('name') == 'ann') returns 1, users.all() returns [{'name': 'ann'}], and subsequent users.insert({'name': 'bob'}) returns 2.
    """
    db = TinyDB(storage=MemoryStorage)
    users = db.table('users')
    
    users.insert({'name': 'ann'})
    
    assert users.search(where('name') == 'ann') == [{'name': 'ann'}]
    assert users.count(where('name') == 'ann') == 1
    assert users.all() == [{'name': 'ann'}]
    
    doc_id = users.insert({'name': 'bob'})
    assert doc_id == 2
