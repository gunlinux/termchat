import argparse
import asyncio

from termchat.app import MessageBus
from termchat.domain.message import Message


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
        metavar="URL",
        help="YouTube live stream or video URL",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Use rich textual TUI instead of plain stdout",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    from termchat.domain.provider import Provider

    providers: list[Provider] = []

    if args.twitch:
        import os
        from termchat.providers.twitch import TwitchProvider

        oauth = os.environ.get("TWITCH_OAUTH", "")
        providers.append(TwitchProvider(args.twitch, oauth))

    if args.youtube:
        from termchat.providers.youtube import YouTubeProvider

        providers.append(YouTubeProvider(args.youtube))

    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus(providers, queue)

    if args.tui:
        from termchat.ui.tui import TermchatApp

        app = TermchatApp(bus, queue)
        await app.run_async()
    else:
        from termchat.ui.terminal import TerminalUI

        ui = TerminalUI(queue)
        bus_task = asyncio.create_task(bus.run())
        ui_task = asyncio.create_task(ui.run())
        await bus_task
        await queue.join()
        ui_task.cancel()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.twitch and not args.youtube:
        parser.error("at least one of --twitch or --youtube is required")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
