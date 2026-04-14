import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional

from pookiedb.exceptions import ValidationError


class Field:
    """Base class for all Pookie model fields."""

    # Used for ordering fields in model definition order
    _creation_counter = 0

    def __init__(
        self,
        *,
        null: bool = False,
        blank: bool = False,
        default=None,
        unique: bool = False,
        db_index: bool = False,
        db_column: str = None,
        primary_key: bool = False,
        editable: bool = True,
        help_text: str = "",
        choices: list = None,
        verbose_name: str = None,
    ):
        self.null = null
        self.blank = blank
        self.default = default
        self.unique = unique
        self.db_index = db_index
        self.db_column = db_column
        self.primary_key = primary_key
        self.editable = editable
        self.help_text = help_text
        self.choices = choices or []
        self.verbose_name = verbose_name

        # Set by ModelBase metaclass
        self.name: str = ""
        self.model = None

        self._creation_order = Field._creation_counter
        Field._creation_counter += 1

    def contribute_to_class(self, model, name: str):
        """Called by the metaclass when the field is attached to a model."""
        self.name = name
        self.model = model
        if not self.db_column:
            self.db_column = name
        if not self.verbose_name:
            self.verbose_name = name.replace("_", " ")

    def get_column_name(self) -> str:
        return self.db_column or self.name

    def to_python(self, value):
        """Convert a raw DB value to a Python object."""
        return value

    def to_db(self, value):
        """Convert a Python object to a DB-safe value."""
        return value

    def validate(self, value, model_instance):
        if value is None:
            if not self.null and not self.primary_key:
                raise ValidationError(f"Field '{self.name}' cannot be null.", field=self.name)
        if self.choices and value is not None:
            valid = [c[0] for c in self.choices]
            if value not in valid:
                raise ValidationError(
                    f"Value '{value}' is not a valid choice for field '{self.name}'.",
                    field=self.name,
                )

    def get_default(self):
        if callable(self.default):
            return self.default()
        return self.default

    def sql_type(self, engine: str) -> str:
        raise NotImplementedError

    def sql_definition(self, engine: str) -> str:
        col = self.get_column_name()
        type_sql = self.sql_type(engine)
        parts = [f'"{col}" {type_sql}']
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.null and not self.primary_key:
            parts.append("NOT NULL")
        if self.unique and not self.primary_key:
            parts.append("UNIQUE")
        if self.default is not None and not callable(self.default):
            val = self.to_db(self.default)
            if isinstance(val, str):
                parts.append(f"DEFAULT '{val}'")
            elif isinstance(val, bool):
                # SQLite uses 0/1; PostgreSQL uses TRUE/FALSE
                parts.append(f"DEFAULT {val}")  # bool renders as True/False in f-string
            elif val is not None:
                parts.append(f"DEFAULT {val}")
        return " ".join(parts)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.name}>"


# ── Auto / Primary Key Fields ────────────────────────────────────────────────

class AutoField(Field):
    def __init__(self, **kwargs):
        kwargs.setdefault("primary_key", True)
        kwargs.setdefault("editable", False)
        super().__init__(**kwargs)

    def sql_type(self, engine: str) -> str:
        if engine == "postgresql":
            return "SERIAL"
        return "INTEGER"  # SQLite uses INTEGER PRIMARY KEY for autoincrement

    def sql_definition(self, engine: str) -> str:
        col = self.get_column_name()
        if engine == "sqlite":
            return f'"{col}" INTEGER PRIMARY KEY AUTOINCREMENT'
        return f'"{col}" SERIAL PRIMARY KEY'


class BigAutoField(AutoField):
    def sql_type(self, engine: str) -> str:
        if engine == "postgresql":
            return "BIGSERIAL"
        return "INTEGER"

    def sql_definition(self, engine: str) -> str:
        col = self.get_column_name()
        if engine == "sqlite":
            return f'"{col}" INTEGER PRIMARY KEY AUTOINCREMENT'
        return f'"{col}" BIGSERIAL PRIMARY KEY'


# ── String Fields ────────────────────────────────────────────────────────────

class CharField(Field):
    def __init__(self, max_length: int = 255, **kwargs):
        self.max_length = max_length
        super().__init__(**kwargs)

    def sql_type(self, engine: str) -> str:
        return f"VARCHAR({self.max_length})"

    def to_python(self, value):
        if value is None:
            return None
        return str(value)

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if value is not None and len(str(value)) > self.max_length:
            raise ValidationError(
                f"Field '{self.name}' exceeds max_length of {self.max_length}.",
                field=self.name,
            )


class TextField(Field):
    def sql_type(self, engine: str) -> str:
        return "TEXT"

    def to_python(self, value):
        return str(value) if value is not None else None


class EmailField(CharField):
    EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 254)
        super().__init__(**kwargs)

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if value and not self.EMAIL_RE.match(value):
            raise ValidationError(f"'{value}' is not a valid email address.", field=self.name)


class URLField(CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 2048)
        super().__init__(**kwargs)

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if value and not (value.startswith("http://") or value.startswith("https://")):
            raise ValidationError(f"'{value}' is not a valid URL.", field=self.name)


class SlugField(CharField):
    SLUG_RE = re.compile(r"^[-a-zA-Z0-9_]+$")

    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 100)
        super().__init__(**kwargs)

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if value and not self.SLUG_RE.match(value):
            raise ValidationError(
                f"'{value}' is not a valid slug (only letters, numbers, hyphens, underscores).",
                field=self.name,
            )


class UUIDField(Field):
    def __init__(self, auto=False, **kwargs):
        self.auto = auto
        if auto:
            kwargs.setdefault("default", uuid.uuid4)
            kwargs.setdefault("editable", False)
        super().__init__(**kwargs)

    def sql_type(self, engine: str) -> str:
        if engine == "postgresql":
            return "UUID"
        return "TEXT"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

    def to_db(self, value):
        if value is None:
            return None
        return str(value) if isinstance(value, uuid.UUID) else value


# ── Numeric Fields ───────────────────────────────────────────────────────────

class IntegerField(Field):
    def sql_type(self, engine: str) -> str:
        return "INTEGER"

    def to_python(self, value):
        return int(value) if value is not None else None

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if value is not None:
            try:
                int(value)
            except (TypeError, ValueError):
                raise ValidationError(f"'{value}' is not a valid integer.", field=self.name)


class BigIntegerField(IntegerField):
    def sql_type(self, engine: str) -> str:
        return "BIGINT"


class FloatField(Field):
    def sql_type(self, engine: str) -> str:
        return "REAL" if engine == "sqlite" else "DOUBLE PRECISION"

    def to_python(self, value):
        return float(value) if value is not None else None


class DecimalField(Field):
    def __init__(self, max_digits: int = 10, decimal_places: int = 2, **kwargs):
        self.max_digits = max_digits
        self.decimal_places = decimal_places
        super().__init__(**kwargs)

    def sql_type(self, engine: str) -> str:
        return f"NUMERIC({self.max_digits}, {self.decimal_places})"

    def to_python(self, value):
        return Decimal(str(value)) if value is not None else None

    def to_db(self, value):
        return str(value) if isinstance(value, Decimal) else value


# ── Boolean Field ────────────────────────────────────────────────────────────

class BooleanField(Field):
    def sql_type(self, engine: str) -> str:
        return "BOOLEAN" if engine == "postgresql" else "INTEGER"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        return bool(int(value)) if str(value).isdigit() else bool(value)

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        return bool(value)

    def sql_definition(self, engine: str) -> str:
        col = self.get_column_name()
        type_sql = self.sql_type(engine)
        parts = [f'"{col}" {type_sql}']
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.null and not self.primary_key:
            parts.append("NOT NULL")
        if self.unique and not self.primary_key:
            parts.append("UNIQUE")
        if self.default is not None and not callable(self.default):
            if engine == "sqlite":
                parts.append(f"DEFAULT {1 if self.default else 0}")
            else:
                parts.append(f"DEFAULT {'TRUE' if self.default else 'FALSE'}")
        return " ".join(parts)


# ── Date / Time Fields ───────────────────────────────────────────────────────

class DateField(Field):
    def __init__(self, auto_now: bool = False, auto_now_add: bool = False, **kwargs):
        self.auto_now = auto_now
        self.auto_now_add = auto_now_add
        if auto_now or auto_now_add:
            kwargs.setdefault("editable", False)
        super().__init__(**kwargs)

    def sql_type(self, engine: str) -> str:
        return "DATE"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, date):
            return value
        from dateutil import parser as dp
        return dp.parse(str(value)).date()

    def to_db(self, value):
        if isinstance(value, date):
            return value.isoformat()
        return value

    def pre_save(self, instance, add: bool):
        from datetime import date as dt_date
        if self.auto_now or (self.auto_now_add and add):
            value = dt_date.today()
            setattr(instance, self.name, value)
            return value
        return getattr(instance, self.name, None)


class DateTimeField(DateField):
    def sql_type(self, engine: str) -> str:
        return "TIMESTAMP WITH TIME ZONE" if engine == "postgresql" else "TEXT"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        from dateutil import parser as dp
        return dp.parse(str(value))

    def pre_save(self, instance, add: bool):
        from datetime import datetime as dt_datetime, timezone
        if self.auto_now or (self.auto_now_add and add):
            value = dt_datetime.now(tz=timezone.utc)
            setattr(instance, self.name, value)
            return value
        return getattr(instance, self.name, None)


class TimeField(Field):
    def sql_type(self, engine: str) -> str:
        return "TIME"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, time):
            return value
        from dateutil import parser as dp
        return dp.parse(str(value)).time()
