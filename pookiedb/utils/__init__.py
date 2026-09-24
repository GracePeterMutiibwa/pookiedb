import re
import math
import os
import threading
import time
import uuid
from typing import Any


_uuid7_lock = threading.Lock()
_uuid7_last = 0


def uuid7() -> uuid.UUID:
    """
    Time-ordered UUID (RFC 9562): 48-bit unix ms timestamp, version, then random bits.
    Monotonic within the process, so values sort in creation order.
    """
    if hasattr(uuid, "uuid7"):  # Python 3.14+
        return uuid.uuid7()

    global _uuid7_last
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (
        (ms & 0xFFFF_FFFF_FFFF) << 80
        | 0x7 << 76                          # version
        | (rand >> 62 & 0xFFF) << 64         # rand_a (12 bits)
        | 0b10 << 62                         # variant
        | rand & 0x3FFF_FFFF_FFFF_FFFF       # rand_b (62 bits)
    )
    with _uuid7_lock:
        # Same millisecond (or clock went back): step past the last value to keep ordering
        if value <= _uuid7_last:
            value = _uuid7_last + 1
        _uuid7_last = value
    return uuid.UUID(int=value)


def slugify(value: str) -> str:
    """Convert a string to a URL-friendly slug."""
    value = str(value).strip().lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value)
    value = re.sub(r"^-+|-+$", "", value)
    return value


def camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


class Paginator:
    """
    Paginate a QuerySet or list.

    Usage:
        p = Paginator(MyModel.objects.all(), per_page=20)
        page = p.page(1)
        print(page.object_list)
        print(page.has_next())
    """

    def __init__(self, object_list, per_page: int):
        self.object_list = object_list
        self.per_page = per_page

    @property
    def count(self) -> int:
        try:
            return self.object_list.count()
        except AttributeError:
            return len(self.object_list)

    @property
    def num_pages(self) -> int:
        if self.count == 0:
            return 1
        return math.ceil(self.count / self.per_page)

    @property
    def page_range(self):
        return range(1, self.num_pages + 1)

    def page(self, number: int) -> "Page":
        if number < 1 or number > self.num_pages:
            raise ValueError(f"Page {number} is out of range (1–{self.num_pages}).")
        offset = (number - 1) * self.per_page
        try:
            objects = list(self.object_list.offset(offset).limit(self.per_page))
        except AttributeError:
            objects = list(self.object_list[offset : offset + self.per_page])
        return Page(objects, number, self)


class Page:
    def __init__(self, object_list: list, number: int, paginator: Paginator):
        self.object_list = object_list
        self.number = number
        self.paginator = paginator

    def has_next(self) -> bool:
        return self.number < self.paginator.num_pages

    def has_previous(self) -> bool:
        return self.number > 1

    def next_page_number(self) -> int:
        if not self.has_next():
            raise ValueError("No next page.")
        return self.number + 1

    def previous_page_number(self) -> int:
        if not self.has_previous():
            raise ValueError("No previous page.")
        return self.number - 1

    def __repr__(self):
        return f"<Page {self.number} of {self.paginator.num_pages}>"
