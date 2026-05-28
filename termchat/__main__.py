import argparse
import asyncio
import os
import signal
import sys
from datetime import UTC, datetime

from termchat.app import MessageBus
from termchat.domain.message import Message
from termchat.domain.provider import Provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="termchat",
        description="Async read-only terminal multi-chat aggregator",
    )
    parser.add_argument(
        "--twitch",
        metavar="CHANNEL",
        help="Twitch channel to read (requires TWITCH_OAUTH env var)",
    )
    parser.add_argument(
        "--youtube",
        metavar="CHANNEL",
        help="YouTube channel handle (resolves to its active live stream)",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Use rich textual TUI instead of plain stdout",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with fake provider (for testing/demo purposes)",
    )
    return parser


def _print_summary(bus: MessageBus) -> None:
    counts = bus.counts
    if counts:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"\nMessages received: {parts}")
    else:
        print("\nMessages received: none")


async def _run(args: argparse.Namespace) -> None:
    providers: list[Provider] = []

    if args.demo:
        from termchat.providers.fake import FakeProvider

        fake_msgs = [
            Message(
                id=str(i),
                author="demo_user",
                text=f"Demo message {i}",
                timestamp=datetime.now(UTC),
                platform="fake",
            )
            for i in range(10)
        ]
        providers.append(FakeProvider(fake_msgs, delay=0.3))

    if args.twitch:
        # lazy: pulls in the IRC/emote stack only when Twitch is requested
        from termchat.providers.twitch import TwitchProvider

        providers.append(TwitchProvider(args.twitch, os.environ.get("TWITCH_OAUTH", "")))

    if args.youtube:
        # lazy: pulls in the httpx live-chat poller only when YouTube is requested
        from termchat.providers.youtube import YouTubeProvider

        providers.append(YouTubeProvider(args.youtube))

    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus(providers, queue)
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    if args.tui:
        # lazy: avoid importing the heavy textual stack unless --tui is used
        from termchat.ui.tui import TermchatApp

        app = TermchatApp(bus, queue)
        try:
            await app.run_async()
        finally:
            _print_summary(bus)
        return

    from termchat.ui.terminal import TerminalUI

    ui = TerminalUI(queue)
    bus_task = asyncio.create_task(bus.run())
    ui_task = asyncio.create_task(ui.run())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    await asyncio.wait(
        [bus_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in (bus_task, ui_task, shutdown_task):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass  # swallow cancellation (and any late provider error) during shutdown drain

    # drain any messages still in the queue
    while not queue.empty():
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            break

    _print_summary(bus)


def _apply_config(args: argparse.Namespace) -> None:
    from termchat.config import load_config

    cfg = load_config()
    if not args.twitch and cfg.get("twitch", {}).get("channel"):
        args.twitch = cfg["twitch"]["channel"]
    if not args.youtube and cfg.get("youtube", {}).get("channel"):
        args.youtube = cfg["youtube"]["channel"]


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _apply_config(args)

    if not args.twitch and not args.youtube and not args.demo:
        parser.error("at least one of --twitch, --youtube, or --demo is required")

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
