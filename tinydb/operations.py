"""
A collection of update operations for TinyDB.

They are used for updates like this:

>>> db.update(delete('foo'), where('foo') == 2)

This would delete the ``foo`` field from all documents where ``foo`` equals 2.
"""


from collections.abc import Callable, MutableMapping
from typing import Any, Union


def delete(field: str) -> Callable[[MutableMapping], None]:
    """
    Delete a given field from the document.
    """
    def transform(doc: MutableMapping):
        del doc[field]

    return transform


def add(field: str, n: Union[int, float, str]) -> Callable[[MutableMapping], None]:
    """
    Add ``n`` to a given field in the document.

    Also works for strings (concatenation), matching the documented behavior.
    """
    def transform(doc: MutableMapping):
        doc[field] += n

    return transform


def subtract(field: str, n: Union[int, float]) -> Callable[[MutableMapping], None]:
    """
    Subtract ``n`` to a given field in the document.
    """
    def transform(doc: MutableMapping):
        doc[field] -= n

    return transform


def set(field: str, val: Any) -> Callable[[MutableMapping], None]:
    """
    Set a given field to ``val``.
    """
    def transform(doc: MutableMapping):
        doc[field] = val

    return transform


def increment(field: str) -> Callable[[MutableMapping], None]:
    """
    Increment a given field in the document by 1.
    """
    def transform(doc: MutableMapping):
        doc[field] += 1

    return transform


def decrement(field: str) -> Callable[[MutableMapping], None]:
    """
    Decrement a given field in the document by 1.
    """
    def transform(doc: MutableMapping):
        doc[field] -= 1

    return transform


def append(field: str, val: Any) -> Callable[[MutableMapping], None]:
    """
    Return a function that appends ``val`` to the list stored at ``field`` in a document.

    If ``field`` does not exist in the document, initialize it as a list containing ``val``.

    :param field: The field name to append to.
    :param val: The value to append.
    :return: A transformation function suitable for ``db.update(...)``.
    """
    def transform(doc: MutableMapping):
        if field in doc:
            doc[field].append(val)
        else:
            doc[field] = [val]

    return transform
