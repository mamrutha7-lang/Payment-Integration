"""Realtime session metadata: the `Undergraduate Sessions` / `Postgraduate
Sessions` DynamoDB tables, behind one small protocol.

Postgres (`platform_call_sessions`) is the source of truth and DynamoDB is the
low-latency projection the architecture reads live state from: one item per
call keyed on `session_id`, updated on open, on every heartbeat, on close and
when the recording lands. `MemorySessionStore` is what a deployment with no
table configured gets — the same interface, process-local — so the bridge is
written once and never branches on "is Dynamo on".

Numbers are marshalled through boto3's own `TypeSerializer`, which refuses
floats (DynamoDB has no float type); `_marshal` turns them into `Decimal`
first, via `str()` so 0.1 stays 0.1 and not 0.1000000000000000055.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from ...config import settings

log = logging.getLogger("app.voice_platform.storage.dynamodb")


class SessionStateStore(Protocol):
    name: str

    def put(self, record: dict[str, Any]) -> bool: ...

    def update(self, session_id: str, fields: dict[str, Any]) -> bool: ...

    def get(self, session_id: str) -> dict[str, Any] | None: ...


def _marshal(value: Any) -> Any:
    """Make `value` DynamoDB-serialisable: floats → Decimal, datetimes → ISO
    strings, sets/tuples → lists, and recurse."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str, Decimal, bytes)):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _marshal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_marshal(v) for v in value]
    return str(value)


def _unmarshal(value: Any) -> Any:
    """Decimals back to int/float so the API returns JSON numbers."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _unmarshal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unmarshal(v) for v in value]
    return value


class MemorySessionStore:
    """Process-local. Also the store the tests run against."""

    name = "memory"

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, record: dict[str, Any]) -> bool:
        sid = str(record["session_id"])
        with self._lock:
            self._items[sid] = dict(_marshal(record))
        return True

    def update(self, session_id: str, fields: dict[str, Any]) -> bool:
        with self._lock:
            item = self._items.setdefault(session_id, {"session_id": session_id})
            item.update(_marshal(fields))
        return True

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(session_id)
            return _unmarshal(dict(item)) if item is not None else None


class DynamoSessionStore:
    """One table, `session_id` as the partition key, an `expires_at` TTL."""

    def __init__(self, table_name: str, client: Any, *, ttl_days: int = 180) -> None:
        if not table_name:
            raise ValueError("a table name is required")
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

        self.name = table_name
        self._client = client
        self._ttl_days = int(ttl_days)
        self._ser = TypeSerializer()
        self._de = TypeDeserializer()

    def _item(self, record: dict[str, Any]) -> dict[str, Any]:
        marshalled = _marshal(record)
        if self._ttl_days > 0 and "expires_at" not in marshalled:
            marshalled["expires_at"] = int(time.time()) + self._ttl_days * 86400
        return {k: self._ser.serialize(v) for k, v in marshalled.items()}

    def put(self, record: dict[str, Any]) -> bool:
        try:
            self._client.put_item(TableName=self.name, Item=self._item(record))
            return True
        except Exception as exc:  # noqa: BLE001 - a projection never fails the call
            log.error("DynamoDB put_item failed on %s: %s", self.name, exc)
            return False

    def update(self, session_id: str, fields: dict[str, Any]) -> bool:
        if not fields:
            return True
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        sets: list[str] = []
        for i, (key, value) in enumerate(_marshal(fields).items()):
            names[f"#f{i}"] = key
            values[f":v{i}"] = self._ser.serialize(value)
            sets.append(f"#f{i} = :v{i}")
        try:
            self._client.update_item(
                TableName=self.name,
                Key={"session_id": {"S": session_id}},
                UpdateExpression="SET " + ", ".join(sets),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("DynamoDB update_item failed on %s: %s", self.name, exc)
            return False

    def get(self, session_id: str) -> dict[str, Any] | None:
        try:
            out = self._client.get_item(
                TableName=self.name, Key={"session_id": {"S": session_id}}
            )
        except Exception as exc:  # noqa: BLE001
            log.error("DynamoDB get_item failed on %s: %s", self.name, exc)
            return None
        item = out.get("Item")
        if not item:
            return None
        return _unmarshal({k: self._de.deserialize(v) for k, v in item.items()})


_STORES: dict[str, SessionStateStore] = {}
_STORES_LOCK = threading.Lock()


def session_store_for(degree_level: str, client: Any | None = None) -> SessionStateStore:
    """The store for one degree level: Dynamo when its table is configured,
    otherwise one shared in-memory store per level. Cached, so every handler
    in the process talks to the same object."""
    level = degree_level.strip().upper()
    with _STORES_LOCK:
        store = _STORES.get(level)
        if store is not None:
            return store
        table = settings.platform_dynamo_table(level)
        if table:
            if client is None:
                import boto3

                client = boto3.client("dynamodb", region_name=settings.platform_region or None)
            store = DynamoSessionStore(table, client, ttl_days=settings.platform_dynamo_ttl_days)
        else:
            store = MemorySessionStore()
        _STORES[level] = store
        return store


def reset_stores() -> None:
    """Tests only: forget the cached stores."""
    with _STORES_LOCK:
        _STORES.clear()
