"""
Единая схема событий бэктестера. Все биржи нормализуются в нее.

Одна строка = одно атомарное изменение:
  - сделка (trade);
  - один ценовой уровень из сообщения стакана (snapshot или update).
Сообщение стакана из N уровней разворачивается в N строк с общим msg_id —
бэктестер применяет их пачкой, пока msg_id не сменится.
"""
import pyarrow as pa

# event_type
TRADE = 0
BOOK_SNAPSHOT = 1  # полный срез стакана: перед применением книгу нужно очистить
BOOK_UPDATE = 2    # дифф: size == 0 означает удаление уровня

# side
BUY = 1    # для сделки — агрессор-покупатель; для стакана — bid
SELL = -1  # для сделки — агрессор-продавец;   для стакана — ask

EVENT_SCHEMA = pa.schema([
    pa.field("ts", pa.timestamp("ns", tz="UTC"), nullable=False),
    pa.field("event_type", pa.int8(), nullable=False),
    pa.field("side", pa.int8(), nullable=False),
    pa.field("price", pa.float64(), nullable=False),
    pa.field("size", pa.float64(), nullable=False),
    pa.field("num_orders", pa.int32()),             # только стакан
    pa.field("msg_id", pa.int64(), nullable=False),  # trade_id для сделок, номер сообщения в файле для стакана
    pa.field("level", pa.int16(), nullable=False),   # позиция уровня внутри сообщения стакана, 0 для сделок
])

# Детерминированный порядок событий:
#   1. ts
#   2. при равном ts сделки раньше стакана: апдейт стакана в той же миллисекунде
#      отражает уже исполненную сделку
#   3. msg_id — исходный порядок внутри источника
#   4. level — порядок уровней внутри сообщения
# Биржи отдают время в миллисекундах, поэтому в одном ts много событий
# и без пунктов 2–4 порядок был бы недетерминированным.
SORT_KEYS = ("ts", "is_book", "msg_id", "level")
