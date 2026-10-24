import asyncio
import selectors

import uvicorn

from atguigu_legal.config import get_settings


def create_selector_event_loop() -> asyncio.AbstractEventLoop:
    """Psycopg 异步连接在 Windows 上需要 Selector 事件循环。"""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def serve() -> None:
    settings = get_settings()
    server = uvicorn.Server(
        uvicorn.Config(
            "atguigu_legal.app:app",
            host=settings.api_host,
            port=settings.api_port,
            loop="none",
        )
    )
    await server.serve()


if __name__ == "__main__":
    asyncio.run(serve(), loop_factory=create_selector_event_loop)
