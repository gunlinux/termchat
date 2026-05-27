import asyncio
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

from termchat.domain.message import Message
from termchat.ui.terminal import TerminalUI


def _msg(i: int, platform: str = "twitch") -> Message:
    return Message(
        id=str(i),
        author="streamer",
        text=f"hello {i}",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform=platform,
    )


async def test_terminal_ui_formats_message():
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(_msg(0))
    ui = TerminalUI(queue)

    output = StringIO()
    with patch("builtins.print", side_effect=lambda *a, **kw: output.write(a[0] + "\n")):
        task = asyncio.create_task(ui.run())
        await queue.join()
        task.cancel()

    assert "[twitch] streamer: hello 0" in output.getvalue()


async def test_terminal_ui_multiple_messages():
    queue: asyncio.Queue[Message] = asyncio.Queue()
    for i in range(3):
        await queue.put(_msg(i))

    output = StringIO()
    with patch("builtins.print", side_effect=lambda *a, **kw: output.write(a[0] + "\n")):
        ui = TerminalUI(queue)
        task = asyncio.create_task(ui.run())
        await queue.join()
        task.cancel()

    lines = output.getvalue().strip().splitlines()
    assert len(lines) == 3
    assert lines[0] == "[twitch] streamer: hello 0"
    assert lines[2] == "[twitch] streamer: hello 2"
