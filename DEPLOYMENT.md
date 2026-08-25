# 🤖 Intelligence Hub: Deployment Guide (Zero Cost)

This project is a curated AI intelligence hub that monitors financial assets, news, and currency rates, delivering alerts via Discord.

## 🚀 Quick Start: Zero-Cost Deployment via GitHub Actions

This setup uses GitHub Actions to run the bot daily for free, removing the need for a 24/7 powered-on PC.

### 1. Repository Setup
1. Create a **Private** repository on GitHub.
2. Push all files from the `agent-services` directory, **including** the YAML configs under `agent-services/config/`. The committed configs ship with empty `webhook_url` fields by design — secrets are injected at runtime via GitHub Secrets.
3. **Branch requirement**: GitHub Actions `on.schedule` events only fire against the repository's **default branch**. The default branch must contain the `agent-services/` directory and the workflow file. Either:
   - Develop on the default branch, or
   - Rename the branch you develop on to be the default (Settings → Branches → Default branch), or
   - Merge your dev branch into the default branch after every change.

### 2. Configure GitHub Secrets
Navigate to `Settings` → `Secrets and variables` → `Actions` and add the following secrets:

| Secret Name | Value | Description |
| :--- | :--- | :--- |
| `DISCORD_WEBHOOK_URL` | `https://discord.com/api/webhooks/...` | Your primary Discord webhook URL. `run_all.py` resolves this into any config that has an empty `webhook_url`. |
| `NEWS_DIGEST_AI__PROVIDER` | `fallback` | Set to `fallback` for zero cost, or `anthropic`/`openai` if you have keys |

### 3. Automation Workflow
Create the file `.github/workflows/daily_intelligence.yml` with the provided YAML configuration. This triggers the bot every day at 00:00 UTC.

### 4. (Optional) Run the Discord Bot
The interactive slash-command bot in `agent-services/bot.py` is not used by the daily workflow. To run it locally or on a host:
```bash
pip install "agent-services[bot]"
DISCORD_BOT_TOKEN=... python -m bot
```
The `[bot]` extra installs `discord.py`, which is otherwise an optional dependency to keep the workflow image small.

---

## 🏗 System Architecture

### 🧩 Core Components
- **Discovery Agent**: Autonomously scans for trending financial assets and RSS feeds, updating the system's focus without manual intervention.
- **News Digest**: Fetches headlines, cleans HTML, and produces a summarized markdown report.
- **ETF Signal Agent**: Calculates technical indicators (RSI, SMA, Bollinger Bands) to identify entry/exit signals.
- **Currency Notifier**: Monitors FX pairs and alerts on significant rate changes.
- **Discord Notifier**: A bulletproof delivery system using a tiered embed design (Red/Green/Blurple).

### 🛠 Tech Stack
- **Language**: Python 3.11+
- **Configuration**: Pydantic-based dynamic loading (YAML + Environment Variables).
- **Scheduling**: GitHub Actions (Cloud) / `run_all.py` (Local).
- **Infrastructure**: Serverless / Zero-Cost.

## 📁 Project Structure
```text
agent-services/
├── common/            # Shared utilities (Discord, Config, Visualizer)
├── news_digest/        # RSS fetching and AI summarization
├── etf_signal/        # Technical analysis and signal generation
├── currency_notifier/  # FX rate monitoring
└── scripts/            # Entry points (run_all.py)
```
