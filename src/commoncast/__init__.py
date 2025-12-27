"""CommonCast package.

This package provides small utilities used throughout the CommonCast
project. At present it exposes a simple helper used in examples and
smoke tests.

Examples:
>>> from commoncast import hello
>>> hello()
'Hello from commoncast!'

"""

__all__ = ["hello"]


def hello() -> str:
    """Return a greeting string from the CommonCast package.

    :returns: A friendly greeting message identifying the package.

    Examples:
    >>> hello()
    'Hello from commoncast!'
    """
    return "Hello from commoncast!"
