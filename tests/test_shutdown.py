import asyncio
import signal
import subprocess
import sys

import pytest

from termchat.app import MessageBus
from termchat.domain.message import Message
from termchat.domain.provider import Provider


# --- unit tests: count tracking ---


async def test_message_bus_counts_by_platform():
    from datetime import datetime, timezone
    from termchat.providers.fake import FakeProvider

    def _m(i: int, p: str) -> Message:
        return Message(
            id=str(i),
            author="a",
            text="t",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            platform=p,
        )

    providers: list[Provider] = [
        FakeProvider([_m(0, "twitch"), _m(1, "twitch")]),
        FakeProvider([_m(2, "youtube")]),
    ]
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus(providers, queue)
    await bus.run()
    assert bus.counts == {"twitch": 2, "youtube": 1}


# --- integration test: subprocess SIGINT ---


@pytest.mark.timeout(10)
def test_sigint_exits_cleanly():
    proc = subprocess.Popen(
        [sys.executable, "-m", "termchat", "--demo"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    import time

    time.sleep(0.5)
    proc.send_signal(signal.SIGINT)
    stdout, stderr = proc.communicate(timeout=8)

    assert proc.returncode == 0, (
        f"exit code was {proc.returncode}\nstderr: {stderr.decode()}"
    )
    assert b"Messages received" in stdout, f"no summary in stdout: {stdout.decode()}"
    assert b"Traceback" not in stderr, f"traceback in stderr: {stderr.decode()}"
