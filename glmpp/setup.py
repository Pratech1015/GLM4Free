#!/usr/bin/env python3
"""
Z.ai Credentials Setup

Run this once to extract and save credentials via browser.
After this, the API client works without any browser dependency.

Usage:
    cd glmpp && python setup.py            # headless browser
    cd glmpp && python setup.py --visible  # show browser window
"""

import sys
import asyncio

try:
    from api import ZaiApiClient
except ImportError:
    from glmpp.api import ZaiApiClient


async def main():
    headless = "--visible" not in sys.argv
    mode = "headless" if headless else "visible"

    print(f"Launching {mode} browser to extract credentials...")
    print("(This only needs to run once)")

    try:
        client = await ZaiApiClient.setup(headless=headless, save=True)
        print(f"\nCredentials saved successfully!")
        print(f"Token: {client.token[:20]}...")
        print(f"Captcha param: {'yes' if client.captcha_verify_param else 'no'}")
        print("\nYou can now use the API without a browser:")
        print("  client = ZaiApiClient.auto_init()")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
