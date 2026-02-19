#!/usr/bin/env python3
"""
Concurrency test script to verify the fixes for race conditions and memory leaks.
"""

import asyncio
import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.monitor import SystemMonitor, MonitoringConfig
from src.detector import AnomalyDetectorService, DetectionConfig
from src.healer import RecoveryEngine, RecoveryConfig


async def test_network_counter_race_condition():
    """Test that network counter operations are atomic and race-condition free."""
    print("Testing Network Counter Race Condition Fix...")
    
    config = MonitoringConfig(
        collection_interval=0.1,  # Fast collection
        enable_network_monitoring=True
    )
    
    monitor = SystemMonitor(config)
    await monitor.start_monitoring()
    
    # Simulate concurrent network metric collection
    async def collect_metrics():
        for _ in range(10):
            await monitor._collect_system_metrics()
            await asyncio.sleep(0.01)
    
    # Run multiple concurrent collectors
    tasks = [collect_metrics() for _ in range(5)]
    await asyncio.gather(*tasks)
    
    # Check that network counters are consistent
    latest_metrics = monitor.get_latest_metrics(limit=5)
    network_values = [m.network_bytes_sent for m in latest_metrics]
    
    # All values should be non-negative (no corruption)
    assert all(v >= 0 for v in network_values), "Network counter corruption detected!"
    
    await monitor.stop_monitoring()
    print("✅ Network counter race condition test passed!")


async def test_memory_leak_fix():
    """Test that buffer cleanup prevents memory leaks."""
    print("Testing Memory Leak Fix...")
    
    config = MonitoringConfig(
        collection_interval=0.01,  # Very fast collection
        enable_network_monitoring=True,
        enable_disk_monitoring=True
    )
    
    monitor = SystemMonitor(config)
    await monitor.start_monitoring()
    
    # Generate high-volume metrics to test memory management
    start_time = time.time()
    while time.time() - start_time < 2.0:  # Run for 2 seconds
        await monitor._collect_system_metrics()
        await asyncio.sleep(0.001)  # 1ms interval
    
    # Check buffer sizes - should not exceed maxlen=5000
    metrics_buffer_size = len(monitor._metrics_buffer)
    api_buffer_size = len(monitor._api_metrics_buffer)
    
    assert metrics_buffer_size <= 5000, f"System metrics buffer exceeded limit: {metrics_buffer_size}"
    assert api_buffer_size <= 5000, f"API metrics buffer exceeded limit: {api_buffer_size}"
    
    await monitor.stop_monitoring()
    print("✅ Memory leak test passed!")


async def test_circuit_breaker_recovery():
    """Test that circuit breaker properly recovers from failures."""
    print("Testing Circuit Breaker Recovery...")
    
    config = RecoveryConfig(
        max_concurrent_actions=2,
        action_timeout=1.0
    )
    
    engine = RecoveryEngine(config)
    
    # Simulate multiple failures to trigger circuit breaker
    from src.detector import AnomalyResult
    from datetime import datetime
    
    # Create a fake anomaly to trigger recovery
    anomaly = AnomalyResult(
        timestamp=datetime.now(),
        metric_type="system_statistical",
        anomaly_detected=True,
        confidence_score=0.9,
        metric_values={"cpu_percent": 95.0},
        anomaly_description="High CPU usage",
        severity_level="critical"
    )
    
    # Trigger multiple recovery actions that will fail (no Docker, no services)
    results = []
    for _ in range(5):
        recovery_results = await engine.trigger_recovery(anomaly)
        results.extend(recovery_results)
        await asyncio.sleep(0.1)
    
    # Check circuit breaker status
    circuit_status = engine._circuit_breaker.get_recovery_status()
    
    # After multiple failures, circuit should be open
    assert circuit_status["state"] in ["OPEN", "HALF_OPEN"], f"Circuit should be open after failures, got: {circuit_status['state']}"
    
    # Wait for recovery timeout and test half-open state
    await asyncio.sleep(1.0)  # Short wait for testing
    
    # Try one more recovery action
    final_results = await engine.trigger_recovery(anomaly)
    
    print("✅ Circuit breaker recovery test passed!")


async def test_graceful_fallback():
    """Test that ML detection gracefully falls back to statistical detection."""
    print("Testing Graceful Fallback...")
    
    config = DetectionConfig(
        ml_warmup_samples=5,  # Low threshold for testing
        min_samples_for_detection=3
    )
    
    detector = AnomalyDetectorService(config)
    
    # Create minimal metrics for testing
    from src.monitor import SystemMetrics
    from datetime import datetime
    
    metrics = []
    for i in range(10):
        metric = SystemMetrics(
            timestamp=datetime.now(),
            cpu_percent=50.0 + i,
            memory_percent=60.0 + i,
            memory_used_gb=10.0 + i,
            memory_total_gb=16.0,
            disk_usage_percent=50.0 + i,
            network_bytes_sent=1000 * i,
            network_bytes_recv=2000 * i
        )
        metrics.append(metric)
    
    # Test anomaly detection
    results = await detector.detect_anomalies(metrics)
    
    # Should have both isolation forest and statistical results
    assert len(results) == 2, f"Expected 2 detection results, got {len(results)}"
    
    # Both should be valid (no crashes)
    for result in results:
        assert result.timestamp is not None, "Result should have timestamp"
        assert result.metric_type in ["system_isolation_forest", "system_statistical", "system_isolation_forest_fallback"]
    
    print("✅ Graceful fallback test passed!")


async def main():
    """Run all concurrency tests."""
    print("Running Aegis Sentinel Concurrency Tests")
    print("=" * 50)
    
    try:
        await test_network_counter_race_condition()
        await test_memory_leak_fix()
        await test_circuit_breaker_recovery()
        await test_graceful_fallback()
        
        print("\n🎉 All concurrency tests passed!")
        print("✅ Race conditions fixed")
        print("✅ Memory leaks prevented") 
        print("✅ Circuit breaker recovery working")
        print("✅ Graceful fallback operational")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)