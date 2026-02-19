#!/usr/bin/env python3
"""
Test script to check Aegis Sentinel status.
"""

import asyncio
import sys
import os

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.main import AegisSentinel
from src.monitor import MonitoringConfig
from src.detector import DetectionConfig
from src.healer import RecoveryConfig


async def test_status():
    """Test the Aegis Sentinel status."""
    try:
        # Create configuration
        monitoring_config = MonitoringConfig(
            collection_interval=2.0,  # Faster for testing
            api_endpoints=[],  # No API endpoints for now
            enable_network_monitoring=True,
            enable_disk_monitoring=True
        )
        
        detection_config = DetectionConfig(
            isolation_contamination=0.1,
            isolation_n_estimators=100,
            statistical_threshold_multiplier=3.0,
            min_samples_for_detection=10,  # Lower for testing
            ml_warmup_samples=10  # Lower for testing
        )
        
        recovery_config = RecoveryConfig(
            enable_docker_recovery=True,
            enable_cache_recovery=True,
            enable_service_recovery=True,
            max_concurrent_actions=3,
            action_timeout=30.0,
            retry_enabled=True
        )
        
        # Create and start sentinel
        sentinel = AegisSentinel(monitoring_config, detection_config, recovery_config)
        await sentinel.start()
        
        # Wait a bit for metrics collection
        await asyncio.sleep(3)
        
        # Get status
        status = sentinel.get_status()
        print("Aegis Sentinel Status:")
        print("=" * 50)
        for key, value in status.items():
            if isinstance(value, dict):
                print(f"{key}:")
                for subkey, subvalue in value.items():
                    print(f"  {subkey}: {subvalue}")
            else:
                print(f"{key}: {value}")
        
        # Stop sentinel
        await sentinel.stop()
        print("\nTest completed successfully!")
        
    except Exception as e:
        print(f"Error during test: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_status())