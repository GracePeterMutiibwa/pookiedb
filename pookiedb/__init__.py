from importlib import metadata

from pookiedb.models.base import Model
from pookiedb.fields.core import (
    Field, CharField, TextField, IntegerField, BigIntegerField,
    FloatField, DecimalField, BooleanField, DateField, DateTimeField,
    TimeField, EmailField, URLField, SlugField, UUIDField, AutoField, BigAutoField,
    AutoUUIDField,
)
from pookiedb.fields.related import (
    ForeignKey, OneToOneField, ManyToManyField,
    CASCADE, SET_NULL, SET_DEFAULT, PROTECT, DO_NOTHING,
)
from pookiedb.fields.special import JSONField, ArrayField
from pookiedb.db.connection import connect, get_connection, transaction, execute
from pookiedb.db.registry import registry
from pookiedb.exceptions import (
    PookieError, DoesNotExist, MultipleObjectsReturned,
    ValidationError, FieldError, MigrationError,
)

try:
    # Single source of truth: the version in pyproject.toml
    __version__ = metadata.version("pookiedb")
except metadata.PackageNotFoundError:  # source checkout that was never installed
    __version__ = "unknown"
__author__ = "Grace Peter Mutiibwa"

__all__ = [
    "Model",
    "Field", "CharField", "TextField", "IntegerField", "BigIntegerField",
    "FloatField", "DecimalField", "BooleanField", "DateField", "DateTimeField",
    "TimeField", "EmailField", "URLField", "SlugField", "UUIDField",
    "AutoField", "BigAutoField", "AutoUUIDField",
    "ForeignKey", "OneToOneField", "ManyToManyField",
    "CASCADE", "SET_NULL", "SET_DEFAULT", "PROTECT", "DO_NOTHING",
    "JSONField", "ArrayField",
    "connect", "get_connection", "transaction", "execute", "registry",
    "PookieError", "DoesNotExist", "MultipleObjectsReturned",
    "ValidationError", "FieldError", "MigrationError",
]
