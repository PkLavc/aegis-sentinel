#!/usr/bin/env python3
"""
Entry point script for Aegis Sentinel.

This script provides a proper entry point for running Aegis Sentinel
with the correct Python path configuration.
"""

import sys
import os

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Now import and run the main module
if __name__ == "__main__":
    from main import main
    import asyncio
    asyncio.run(main())