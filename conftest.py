"""
Test configuration for Aegis Sentinel.

This module configures the test environment and fixes import path issues
for the test suite to work correctly in any environment.
"""

import sys
import os

# Add the src directory to Python path for tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Configure pytest settings
def pytest_configure(config):
    """Configure pytest settings."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )

def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers."""
    import pytest
    for item in items:
        # Add slow marker to tests that might be slow
        if "slow" in item.nodeid:
            item.add_marker(pytest.mark.slow)
