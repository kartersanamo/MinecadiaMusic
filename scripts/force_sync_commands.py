#!/usr/bin/env python3
"""One-shot: re-sync MinecadiaMusic slash commands."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from main import Client  # noqa: E402


async def _run() -> None:
    client = Client()

    @client.event
    async def on_ready() -> None:
        synced = await client.sync_command_tree()
        print(f"Synced {len(synced)} global commands.")
        music = client.tree.get_command("music")
        print(f"/music registered: {music is not None}")
        await client.close()

    await client.start(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    asyncio.run(_run())
