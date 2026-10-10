import pyarrow as pa
from src.normalizers.schema import (
    BOOK_SNAPSHOT,
    BOOK_UPDATE,
    BUY,
    EVENT_SCHEMA,
    SELL,
    SORT_KEYS,
    TRADE,
)


def test_constants():
    assert TRADE == 0
    assert BOOK_SNAPSHOT == 1
    assert BOOK_UPDATE == 2

    assert BUY == 1
    assert SELL == -1

    assert SORT_KEYS == ("ts", "is_book", "msg_id", "level")


def test_event_schema_fields():
    assert isinstance(EVENT_SCHEMA, pa.Schema)

    field_names = EVENT_SCHEMA.names
    assert field_names == ["ts", "event_type", "side", "price", "size", "num_orders", "msg_id", "level"]

    assert EVENT_SCHEMA.field("ts").type == pa.timestamp("ns", tz="UTC")
    assert not EVENT_SCHEMA.field("ts").nullable

    assert EVENT_SCHEMA.field("event_type").type == pa.int8()
    assert not EVENT_SCHEMA.field("event_type").nullable

    assert EVENT_SCHEMA.field("side").type == pa.int8()
    assert not EVENT_SCHEMA.field("side").nullable

    assert EVENT_SCHEMA.field("price").type == pa.float64()
    assert not EVENT_SCHEMA.field("price").nullable

    assert EVENT_SCHEMA.field("size").type == pa.float64()
    assert not EVENT_SCHEMA.field("size").nullable

    assert EVENT_SCHEMA.field("num_orders").type == pa.int32()
    assert EVENT_SCHEMA.field("num_orders").nullable  # Nullable for trades

    assert EVENT_SCHEMA.field("msg_id").type == pa.int64()
    assert not EVENT_SCHEMA.field("msg_id").nullable

    assert EVENT_SCHEMA.field("level").type == pa.int16()
    assert not EVENT_SCHEMA.field("level").nullable
