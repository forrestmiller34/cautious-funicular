"""Notification helpers for ingestion scripts."""

import logging
import os

import requests
from dotenv import load_dotenv  

load_dotenv() 
logger = logging.getLogger(__name__)


def notify(message: str) -> None:
    """Send a message to the configured Discord webhook, if available."""
    webhook_url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not webhook_url:
        logger.debug("NOTIFY_WEBHOOK_URL is not set; skipping notification.")
        return

    payload = {"content": message[:2000]}
    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to send notification: %s", exc)

