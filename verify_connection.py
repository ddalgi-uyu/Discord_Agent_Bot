import asyncio
import os
import sys
from pathlib import Path

# Setup path for imports
os.environ["PYTHONPATH"] = "C:\\Users\\cyberspace\\Desktop\\opencode_project\\agent-services"
sys.path.append("C:\\Users\cyberspace\Desktop\opencode_project\agent-services")

from common.discord_notifier import WebhookNotifier, Embed, EmbedField

async def send_test_alert():
    webhook_url = "https://discordapp.com/api/webhooks/1536577285581307964/AOcJGcEypXikaKeGKCEGsqSp-QJL53owqwvHqKn7ybaXfcQBd7Rp5GkesRoPN5HDhLJ4"
    
    print(f"Sending verification alert to Discord...")
    async with WebhookNotifier(webhook_url=webhook_url) as notifier:
        embed = Embed(
            title="🚀 Intelligence Hub Online",
            description="Sisyphus has successfully connected to your Discord server!",
            fields=[
                EmbedField(name="Status", value="Connected", inline=True),
                EmbedField(name="Mode", value="Webhook", inline=True),
                EmbedField(name="Branch", value="feature/agent-services", inline=True),
            ],
            footer="Infrastructure Verification Complete"
        )
        success = await notifier.send_embed(embed)
        if success:
            print("Success! Check your Discord channel.")
        else:
            print("Delivery failed. Please check the Webhook URL.")

if __name__ == "__main__":
    asyncio.run(send_test_alert())
