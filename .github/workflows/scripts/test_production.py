"""
Tests (Production) - Send test links and verify the production bot responds.

This script uses a separate test bot token to send links in a test channel,
then checks if the production ClyppyBot instance replies to each one.
"""

import asyncio
import json
import os
import sys

import discord

PRODUCTION_BOT_ID = 1111723928604381314
TIMEOUT_SECONDS = 120
POLL_INTERVAL = 5

TEST_LINKS = [
    ("Twitch Clip", "https://www.twitch.tv/forsen/clip/KathishFaintFriesGingerPower-IWV6AJsEbYlFzRA8"),
    ("Kick Clip", "https://kick.com/xqc/clips/clip_01KMGPYW5ZNJAAWXPB8VK2B3S0"),
    ("Medal Clip", "https://medal.tv/games/elden-ring-nightreign/clips/lYTMyEsLoZGnsjRbL"),
    ("YouTube Clip", "https://youtube.com/clip/UgkxqzFvVu9v0OYaCVL74C83YmI3eOvCMWuD?si=YnxM8ffPJKI8odd-"),
    ("YouTube Short", "https://www.youtube.com/shorts/QH6NTz88iI0"),
    ("YouTube Video", "https://www.youtube.com/watch?v=cV9XQm9h6mU"),
    ("Google Drive", "https://drive.google.com/file/d/1lIQg1G-0SLR9ut6N4vrEG_DI6rTINE_A/view?usp=drive_link"),
    ("TikTok", "https://www.tiktok.com/@mertbaglarr/video/7618767727255194913?is_from_webapp=1&sender_device=pc"),
    ("Twitter Video", "https://x.com/bilawalsidhu/status/2032432668105712093"),
    ("Instagram Reel", "https://www.instagram.com/reel/DWEEV90CVI7/?utm_source=ig_web_copy_link&igsh=NTc4MTIwNjQ2YQ=="),
]


async def wait_for_bot_reply(channel, test_message, timeout=TIMEOUT_SECONDS):
    """Poll the channel for a reply from the production bot to our test message."""
    elapsed = 0
    while elapsed < timeout:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        # Fetch recent messages after our test message
        async for msg in channel.history(after=test_message, limit=50):
            if msg.author.id != PRODUCTION_BOT_ID:
                continue
            # Check if it's a direct reply to our message
            if msg.reference and msg.reference.message_id == test_message.id:
                return msg
            # Also accept any message from the bot that appeared after ours
            # (some platforms may not use reply but post in channel)
            return msg

    return None


async def run_tests():
    token = os.environ.get("TESTS_BOT_TOKEN")
    server_id = os.environ.get("TEST_SERVER")
    channel_id = os.environ.get("TEST_CHANNEL_ID")

    if not all([token, server_id, channel_id]):
        print("::error::Missing required environment variables: TESTS_BOT_TOKEN, TEST_SERVER, TEST_CHANNEL_ID")
        sys.exit(1)

    server_id = int(server_id)
    channel_id = int(channel_id)

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    results = []

    @client.event
    async def on_ready():
        print(f"Test bot connected as {client.user}")

        guild = client.get_guild(server_id)
        print(f"Server: {guild.name if guild else f'UNKNOWN (id: {server_id})'}")

        channel = client.get_channel(channel_id)
        if channel is None:
            print(f"::error::Could not find channel {channel_id}")
            await client.close()
            return

        print(f"Channel: #{channel.name}")

        for platform_name, link in TEST_LINKS:
            print(f"\nTesting {platform_name}: {link}")

            # Send the link
            test_msg = await channel.send(link)
            print(f"  Sent message {test_msg.id}, waiting for production bot response...")

            # Wait for production bot to reply
            reply = await wait_for_bot_reply(channel, test_msg)

            if reply:
                print(f"  Got response from production bot (message {reply.id})")
                results.append({"platform": platform_name, "success": True})
            else:
                print(f"  No response after {TIMEOUT_SECONDS}s")
                results.append({"platform": platform_name, "success": False})

            # Small delay between tests
            await asyncio.sleep(3)

        await client.close()

    await client.start(token)

    # Build report
    passed = sum(1 for r in results if r["success"])
    failed = sum(1 for r in results if not r["success"])
    total = len(results)
    success_rate = f"{(passed / total * 100):.0f}%" if total > 0 else "0%"

    warnings = []
    for r in results:
        if not r["success"]:
            warnings.append(f"{r['platform']} link didn't get a response")

    report = {
        "success_rate": success_rate,
        "passed": passed,
        "failed": failed,
        "total": total,
        "warnings": "\n".join(warnings) if warnings else "",
    }

    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed ({success_rate})")
    if warnings:
        print(f"Warnings:\n  " + "\n  ".join(warnings))
    print(f"{'='*50}")

    # Write shields.io badge JSON
    if failed == 0:
        color = "brightgreen"
    elif failed == total:
        color = "red"
    else:
        color = "yellow"

    badge = {
        "schemaVersion": 1,
        "label": "Tests (Production)",
        "message": f"{passed}/{total} passing",
        "color": color,
    }
    with open("badge.json", "w") as f:
        json.dump(badge, f)

    # Emit GitHub Actions annotations
    for w in warnings:
        print(f"::warning::{w}")

    # Write to GitHub Actions summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## Tests (Production) Results\n\n")
            f.write(f"**Success rate: {success_rate}** ({passed}/{total})\n\n")
            if warnings:
                f.write("### Warnings\n\n")
                for w in warnings:
                    f.write(f"- {w}\n")
            else:
                f.write("All platforms responded successfully.\n")
            f.write("\n| Platform | Status |\n|----------|--------|\n")
            for r in results:
                status = "Pass" if r["success"] else "Fail"
                f.write(f"| {r['platform']} | {status} |\n")

    # Exit code: 1 if all failed, 0 otherwise (partial = warnings only)
    if failed == total and total > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run_tests())
