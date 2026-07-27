"""RabbitMQ publisher — the diagram's Ingestion Profile edge.

Diagram topology: a TOPIC exchange with per-customer routing. Events publish with
routing key "customer.<prefix>"; a per-customer worker binds its own queue to just
its customer's key, while the default catch-all queue ("iot.events", bound
"customer.#") receives everything so no event is lost when no dedicated worker runs.

Lazy robust connection: first publish connects, reconnects transparently, close()
is idempotent.
"""

import asyncio
import logging
import re
from typing import Any

import aio_pika

logger = logging.getLogger(__name__)

# aio_pika.abc.Arguments is not re-exported for type checkers; alias it here so the
# shared QUEUE_ARGS constant type-checks at every declare_queue call site.
Arguments = dict[str, Any]

# v2 owns a fully separate namespace on the broker. The Java stack published Java-shaped
# messages straight into `iot.events` with its own `iot.dlx`; those payloads do not parse
# as EventParse (no tenant_id, camelCase deviceId), so sharing either name would dead-letter
# 20k messages and let the two consumers compete. Separate names let both stacks run in
# parallel through cutover.
EXCHANGE_NAME = "v2.events.topic"
CATCH_ALL_QUEUE = "v2.events"
CATCH_ALL_BINDING = "customer.#"
DLX_NAME = "v2.dlx"
DLQ_NAME = "v2.events.dead"
# Every v2 queue carries this, so a rejected message lands in v2's own dead-letter queue.
QUEUE_ARGS: Arguments = {"x-dead-letter-exchange": DLX_NAME}


def routing_key_for(customer: str | None) -> str:
    """AMQP routing keys are dot-delimited words; customer prefixes are sanitized so
    a hostile value cannot inject extra routing segments."""
    if not customer:
        return "customer._unknown"
    return "customer." + re.sub(r"[^A-Za-z0-9_-]", "_", customer)


async def declare_topology(
    channel: aio_pika.abc.AbstractChannel,
) -> aio_pika.abc.AbstractExchange:
    """Exchange + DLX + catch-all queue. Both publisher and consumer declare this
    (idempotent), so whichever side starts first creates it and no message is lost."""
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )
    dlx = await channel.declare_exchange(DLX_NAME, aio_pika.ExchangeType.FANOUT, durable=True)
    dlq = await channel.declare_queue(DLQ_NAME, durable=True)
    await dlq.bind(dlx)
    catch_all = await channel.declare_queue(CATCH_ALL_QUEUE, durable=True, arguments=QUEUE_ARGS)
    await catch_all.bind(exchange, routing_key=CATCH_ALL_BINDING)
    return exchange


class RabbitPublisher:
    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._lock = asyncio.Lock()

    async def _ensure_exchange(self) -> aio_pika.abc.AbstractExchange:
        async with self._lock:
            if self._exchange is None or self._channel is None or self._channel.is_closed:
                if self._connection is None or self._connection.is_closed:
                    self._connection = await aio_pika.connect_robust(self._url)
                self._channel = await self._connection.channel()
                self._exchange = await declare_topology(self._channel)
            return self._exchange

    async def publish(self, body: bytes, customer: str | None = None) -> None:
        exchange = await self._ensure_exchange()
        await exchange.publish(
            aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key=routing_key_for(customer),
        )

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None
        self._channel = None
        self._exchange = None
