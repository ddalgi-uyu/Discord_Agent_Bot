"""Visualisation primitives for agent services.

This module provides utilities to generate charts and plots from time-series
data, specifically designed to be attached as images to Discord embeds.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

logger = logging.getLogger(__name__)

def generate_trend_chart(
    pair: str,
    history: list[tuple[float, datetime]],
    current_rate: float,
    target_rate: float | None = None,
    output_dir: Path = Path("data/charts"),
) -> Path | None:
    """Generate a time-series line chart for a currency pair.

    Returns the path to the generated PNG file, or None if generation failed.
    """
    if not history:
        logger.warning("No history available for %s; skipping chart", pair)
        return None

    try:
        # Use a non-interactive backend for server-side rendering
        plt.switch_backend("Agg")
        
        # Data extraction
        dates = [ts for _, ts in history]
        rates = [r for r, _ in history]
        
        plt.figure(figsize=(10, 5), dpi=100)
        plt.style.use("ggplot") # Modern, clean look
        
        # Plot the trend line
        plt.plot(dates, rates, color="#5865F2", linewidth=2, label="Rate Trend")
        
        # Mark the current price
        plt.scatter(dates[-1], rates[-1], color="red", zorder=5, label="Current")
        
        # Plot target threshold if provided
        if target_rate is not None:
            plt.axhline(y=target_rate, color="green", linestyle="--", alpha=0.6, label=f"Target ({target_rate:.4f})")
        
        # Formatting
        plt.title(f"Trend Analysis: {pair}", fontsize=14, fontweight="bold")
        plt.xlabel("Date", fontsize=10)
        plt.ylabel("Exchange Rate", fontsize=10)
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        # Clean up date labels on X axis
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        plt.gcf().autofmt_xdate()

        # Save file
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"trend_{pair.replace('/', '_')}.png"
        plt.savefig(file_path)
        plt.close()
        
        return file_path
    except Exception as exc:
        logger.exception("Failed to generate trend chart for %s: %s", pair, exc)
        return None

__all__ = ["generate_trend_chart"]
