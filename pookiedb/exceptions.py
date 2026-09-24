
class PookieError(Exception):
    """Base exception for all Pookie errors."""


class DoesNotExist(PookieError):
    """Raised when a queryset lookup finds no results and exactly one was expected."""


class MultipleObjectsReturned(PookieError):
    """Raised when a queryset lookup returns more than one result and exactly one was expected."""


class ValidationError(PookieError):
    """Raised when model field validation fails."""

    def __init__(self, message, field=None):
        self.field = field
        super().__init__(message)


class FieldError(PookieError):
    """Raised when an invalid field name or lookup is used."""


class MigrationError(PookieError):
    """Raised when a migration operation fails."""


class EmbeddingError(PookieError):
    """Raised when the embedding callback fails or breaks its contract."""

    def __init__(self, message, cause=None):
        self.cause = cause
        super().__init__(message)


class ConnectionError(PookieError):
    """Raised when a database connection cannot be established."""


class IntegrityError(PookieError):
    """Raised when a database integrity constraint is violated."""
