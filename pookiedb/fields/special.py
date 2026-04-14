import json
from pookiedb.fields.core import Field
from pookiedb.exceptions import ValidationError


class JSONField(Field):
    """
    Stores arbitrary JSON data.
    - PostgreSQL: uses native JSONB column for efficient querying.
    - SQLite: serializes to TEXT.

    Usage:
        metadata = pookie.JSONField(default=dict)
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def sql_type(self, engine: str) -> str:
        return "JSONB" if engine == "postgresql" else "TEXT"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return value

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if value is not None:
            try:
                if isinstance(value, str):
                    json.loads(value)
            except json.JSONDecodeError:
                raise ValidationError(
                    f"Field '{self.name}' contains invalid JSON.", field=self.name
                )


class ArrayField(Field):
    """
    Stores a list of values.
    - PostgreSQL: uses native ARRAY type.
    - SQLite: serializes to JSON TEXT.

    Usage:
        tags = pookie.ArrayField(base_field=pookie.CharField(max_length=50))
    """

    def __init__(self, base_field: Field = None, size: int = None, **kwargs):
        from pookiedb.fields.core import TextField
        self.base_field = base_field or TextField()
        self.size = size
        super().__init__(**kwargs)

    def sql_type(self, engine: str) -> str:
        if engine == "postgresql":
            base_type = self.base_field.sql_type(engine)
            if self.size:
                return f"{base_type}[{self.size}]"
            return f"{base_type}[]"
        return "TEXT"  # SQLite: JSON-encoded list

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (TypeError, json.JSONDecodeError):
            pass
        return value

    def to_db(self, value):
        if value is None:
            return None
        # For SQLite always serialize; postgres receives native list
        if isinstance(value, list):
            return json.dumps(value)
        return value

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if value is not None and not isinstance(value, (list, str)):
            raise ValidationError(
                f"Field '{self.name}' must be a list.", field=self.name
            )
