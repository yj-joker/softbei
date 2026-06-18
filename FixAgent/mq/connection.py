import logging
import aio_pika
from config.settings import get_settings

logger = logging.getLogger(__name__)

_connection: aio_pika.abc.AbstractRobustConnection | None = None


async def get_connection() -> aio_pika.abc.AbstractRobustConnection:
    global _connection
    if _connection is None or _connection.is_closed:
        settings = get_settings()
        _connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        logger.info("[MQ] 连接建立: %s", settings.rabbitmq_url.split("@")[-1])
    return _connection


async def close_connection():
    global _connection
    if _connection and not _connection.is_closed:
        await _connection.close()
        _connection = None
        logger.info("[MQ] 连接关闭")
