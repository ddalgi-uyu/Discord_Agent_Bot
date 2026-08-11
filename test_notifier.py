import asyncio
from pathlib import Path
from common.discord_notifier import WebhookNotifier, Embed, EmbedField

async def test_notifier():
    # Use a test webhook (this will fail if the URL is invalid, 
    # but we are testing the code path)
    test_url = "https://discord.com/api/webhooks/test-webhook-url"
    
    print(f"Testing WebhookNotifier with URL: {test_url}")
    
    async with WebhookNotifier(webhook_url=test_url) as notifier:
        # Test 1: Plain message
        print("Sending plain message...")
        success_msg = await notifier.send_message("Hello from Sisyphus! 🚀")
        print(f"Message delivery success: {success_msg}")
        
        # Test 2: Embed
        print("Sending embed...")
        embed = Embed(
            title="System Verification",
            description="Testing the Discord Notifier infrastructure.",
            fields=[
                EmbedField(name="Status", value="Running", inline=True),
                EmbedField(name="Agent", value="Sisyphus", inline=True),
            ],
            footer="Infrastructure Test"
        )
        success_embed = await notifier.send_embed(embed)
        print(f"Embed delivery success: {success_embed}")

if __name__ == "__main__":
    asyncio.run(test_notifier())
