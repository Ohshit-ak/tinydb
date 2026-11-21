from tinydb import TinyDB
from tinydb.queries import Query
from tinydb.storages import MemoryStorage


def test_ac1_greater_than_incompatible_types():
    """
    - Given a TinyDB database instance `db` containing a document with a string value for age `{'age': 'forty'}` and a query `Query().age > 30`
    - When calling `db.search(Query().age > 30)`
    - Then it returns an empty list `[]` and does not raise `TypeError`.
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert({'age': 'forty'})
    
    result = db.search(Query().age > 30)
    assert result == []


def test_ac2_less_than_or_equal_incompatible_types():
    """
    - Given a TinyDB database instance `db` containing a document with a string value for age `{'age': 'forty'}` and a query `Query().age <= 50`
    - When calling `db.search(Query().age <= 50)`
    - Then it returns an empty list `[]` and does not raise `TypeError`.
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert({'age': 'forty'})
    
    result = db.search(Query().age <= 50)
    assert result == []


def test_ac3_valid_comparison_queries():
    """
    - Given a TinyDB database instance `db` containing a document with an integer age `{'age': 40}` and a query `Query().age > 30`
    - When calling `db.search(Query().age > 30)`
    - Then it returns the matching document list containing `{'age': 40}`.
    """
    db = TinyDB(storage=MemoryStorage)
    db.insert({'age': 40})
    
    result = db.search(Query().age > 30)
    assert result == [{'age': 40}]
