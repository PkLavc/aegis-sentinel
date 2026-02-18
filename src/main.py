"""
Main entry point for Aegis Sentinel.

This module provides the primary interface for the Aegis Sentinel auto-healing
infrastructure engine. It orchestrates the monitoring, anomaly detection, and
recovery services to provide comprehensive infrastructure resilience.
"""

import asyncio
import logging
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

from .detector import AnomalyDetectorService, DetectionConfig
from .healer import RecoveryEngine, RecoveryConfig
from .monitor import APIMetrics, MonitoringConfig, SystemMetrics, SystemMonitor

logger = logging.getLogger(__name__)


class AegisSentinel:
    """Main Aegis Sentinel service orchestrator."""
    
    def __init__(
        self,
        monitoring_config: Optional[MonitoringConfig] = None,
        detection_config: Optional[DetectionConfig] = None,
        recovery_config: Optional[RecoveryConfig] = None
    ) -> None:
        """Initialize the Aegis Sentinel service."""
        self.monitoring_config = monitoring_config or MonitoringConfig()
        self.detection_config = detection_config or DetectionConfig()
        self.recovery_config = recovery_config or RecoveryConfig()
        
        # Initialize components
        self.monitor = SystemMonitor(self.monitoring_config)
        self.detector = AnomalyDetectorService(self.detection_config)
        self.recovery = RecoveryEngine(self.recovery_config)
        
        # State management
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._detection_task: Optional[asyncio.Task] = None
        self._recovery_task: Optional[asyncio.Task] = None
        
        # Metrics storage
        self._system_metrics: List[SystemMetrics] = []
        self._api_metrics: List[APIMetrics] = []
        
        # Statistics
        self._stats = {
            'anomalies_detected': 0,
            'recovery_actions_triggered': 0,
            'recovery_actions_successful': 0,
            'start_time': None,
            'last_detection_time': None
        }
        
        logger.info("Aegis Sentinel initialized", extra={
            "monitoring_interval": self.monitoring_config.collection_interval,
            "detection_methods": ["isolation_forest", "statistical"],
            "recovery_enabled": self.recovery_config.enable_docker_recovery or 
                               self.recovery_config.enable_cache_recovery or 
                               self.recovery_config.enable_service_recovery
        })
    
    async def start(self) -> None:
        """Start the Aegis Sentinel service."""
        if self._running:
            logger.warning("Aegis Sentinel already running")
            return
        
        self._running = True
        self._stats['start_time'] = datetime.now()
        
        logger.info("Starting Aegis Sentinel service")
        
        # Start monitoring
        await self.monitor.start_monitoring()
        
        # Start detection and recovery loop
        self._detection_task = asyncio.create_task(self._detection_loop())
        
        logger.info("Aegis Sentinel service started successfully")
    
    async def stop(self) -> None:
        """Stop the Aegis Sentinel service."""
        if not self._running:
            logger.warning("Aegis Sentinel not running")
            return
        
        self._running = False
        
        logger.info("Stopping Aegis Sentinel service")
        
        # Stop monitoring
        await self.monitor.stop_monitoring()
        
        # Cancel detection task
        if self._detection_task:
            self._detection_task.cancel()
            try:
                await self._detection_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Aegis Sentinel service stopped")
    
    async def _detection_loop(self) -> None:
        """Main detection and recovery loop."""
        while self._running:
            try:
                # Collect latest metrics
                system_metrics = self.monitor.get_latest_metrics(limit=100)
                api_metrics = self.monitor.get_latest_api_metrics(limit=100)
                
                if not system_metrics:
                    await asyncio.sleep(self.monitoring_config.collection_interval)
                    continue
                
                # Detect system anomalies
                system_anomalies = await self.detector.detect_anomalies(system_metrics)
                
                # Detect API anomalies
                api_anomalies = []
                if api_metrics:
                    api_anomalies = await self.detector.detect_api_anomalies(api_metrics)
                
                # Process anomalies
                all_anomalies = system_anomalies + api_anomalies
                for anomaly in all_anomalies:
                    if anomaly.anomaly_detected:
                        self._stats['anomalies_detected'] += 1
                        self._stats['last_detection_time'] = datetime.now()
                        
                        logger.warning("Anomaly detected", extra={
                            "anomaly_type": anomaly.metric_type,
                            "severity": anomaly.severity_level,
                            "confidence": round(anomaly.confidence_score, 3),
                            "description": anomaly.anomaly_description
                        })
                        
                        # Trigger recovery actions
                        recovery_results = await self.recovery.trigger_recovery(anomaly)
                        
                        self._stats['recovery_actions_triggered'] += len(recovery_results)
                        successful_recoveries = sum(1 for r in recovery_results if r.success)
                        self._stats['recovery_actions_successful'] += successful_recoveries
                        
                        if successful_recoveries > 0:
                            logger.info("Recovery actions completed successfully", extra={
                                "successful_actions": successful_recoveries,
                                "total_actions": len(recovery_results)
                            })
                        else:
                            logger.warning("No recovery actions were successful", extra={
                                "total_actions": len(recovery_results)
                            })
                
                # Update internal metrics storage
                self._system_metrics = system_metrics
                self._api_metrics = api_metrics
                
                # Log periodic status
                if self._stats['anomalies_detected'] % 10 == 0 and self._stats['anomalies_detected'] > 0:
                    await self._log_status()
                
                await asyncio.sleep(self.monitoring_config.collection_interval)
                
            except Exception as e:
                logger.error("Error in detection loop", exc_info=True, extra={
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                })
                await asyncio.sleep(self.monitoring_config.collection_interval)
    
    async def _log_status(self) -> None:
        """Log periodic service status."""
        if self._stats['start_time']:
            uptime = datetime.now() - self._stats['start_time']
            uptime_seconds = uptime.total_seconds()
        else:
            uptime_seconds = 0
        
        latest_metrics = self.monitor.get_metrics_summary()
        
        logger.info("Aegis Sentinel status", extra={
            "uptime_seconds": uptime_seconds,
            "anomalies_detected": self._stats['anomalies_detected'],
            "recovery_actions_triggered": self._stats['recovery_actions_triggered'],
            "recovery_actions_successful": self._stats['recovery_actions_successful'],
            "success_rate": (self._stats['recovery_actions_successful'] / max(self._stats['recovery_actions_triggered'], 1)) * 100,
            "latest_metrics": latest_metrics,
            "last_detection": self._stats['last_detection_time'].isoformat() if self._stats['last_detection_time'] else None
        })
    
    def get_status(self) -> Dict:
        """Get current service status."""
        if self._stats['start_time']:
            uptime = datetime.now() - self._stats['start_time']
            uptime_seconds = uptime.total_seconds()
        else:
            uptime_seconds = 0
        
        latest_metrics = self.monitor.get_metrics_summary()
        
        return {
            "service_status": "running" if self._running else "stopped",
            "uptime_seconds": uptime_seconds,
            "anomalies_detected": self._stats['anomalies_detected'],
            "recovery_actions_triggered": self._stats['recovery_actions_triggered'],
            "recovery_actions_successful": self._stats['recovery_actions_successful'],
            "success_rate": (self._stats['recovery_actions_successful'] / max(self._stats['recovery_actions_triggered'], 1)) * 100,
            "latest_metrics": latest_metrics,
            "last_detection": self._stats['last_detection_time'].isoformat() if self._stats['last_detection_time'] else None,
            "monitoring_config": {
                "collection_interval": self.monitoring_config.collection_interval,
                "api_endpoints_count": len(self.monitoring_config.api_endpoints),
                "network_monitoring": self.monitoring_config.enable_network_monitoring,
                "disk_monitoring": self.monitoring_config.enable_disk_monitoring
            },
            "detection_config": {
                "isolation_contamination": self.detection_config.isolation_contamination,
                "isolation_n_estimators": self.detection_config.isolation_n_estimators,
                "statistical_threshold": self.detection_config.statistical_threshold_multiplier,
                "min_samples": self.detection_config.min_samples_for_detection
            },
            "recovery_config": {
                "docker_recovery": self.recovery_config.enable_docker_recovery,
                "cache_recovery": self.recovery_config.enable_cache_recovery,
                "service_recovery": self.recovery_config.enable_service_recovery,
                "max_concurrent_actions": self.recovery_config.max_concurrent_actions
            }
        }
    
    def get_latest_anomalies(self, limit: int = 10) -> List[Dict]:
        """Get the latest detected anomalies."""
        # This would typically be implemented with a persistent store
        # For now, return empty list as we don't persist anomalies
        return []
    
    def get_recovery_history(self, limit: int = 10) -> List[Dict]:
        """Get the recovery action history."""
        # This would typically be implemented with a persistent store
        # For now, return empty list as we don't persist recovery history
        return []


@asynccontextmanager
async def aegis_sentinel_context(
    monitoring_config: Optional[MonitoringConfig] = None,
    detection_config: Optional[DetectionConfig] = None,
    recovery_config: Optional[RecoveryConfig] = None
):
    """Context manager for Aegis Sentinel service."""
    sentinel = AegisSentinel(monitoring_config, detection_config, recovery_config)
    
    try:
        await sentinel.start()
        yield sentinel
    finally:
        await sentinel.stop()


async def main():
    """Main entry point for Aegis Sentinel."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('aegis_sentinel.log')
        ]
    )
    
    # Create default configuration
    monitoring_config = MonitoringConfig(
        collection_interval=10.0,  # Check every 10 seconds
        api_endpoints=[],  # Add your API endpoints here
        enable_network_monitoring=True,
        enable_disk_monitoring=True
    )
    
    detection_config = DetectionConfig(
        isolation_contamination=0.1,
        isolation_n_estimators=100,
        statistical_threshold_multiplier=3.0,
        min_samples_for_detection=50,
        sliding_window_size=100
    )
    
    recovery_config = RecoveryConfig(
        enable_docker_recovery=True,
        enable_cache_recovery=True,
        enable_service_recovery=True,
        max_concurrent_actions=3,
        action_timeout=120.0,
        retry_enabled=True
    )
    
    # Create signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()
    
    # Set up signal handlers
    if sys.platform != 'win32':  # Windows doesn't support SIGTERM
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler())
    signal.signal(signal.SIGINT, lambda s, f: signal_handler())
    
    # Run Aegis Sentinel
    try:
        async with aegis_sentinel_context(monitoring_config, detection_config, recovery_config) as sentinel:
            logger.info("Aegis Sentinel is running. Press Ctrl+C to stop.")
            
            # Wait for shutdown signal
            await shutdown_event.wait()
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error("Unexpected error in main", exc_info=True, extra={
            "error_type": type(e).__name__,
            "error_message": str(e)
        })
    finally:
        logger.info("Aegis Sentinel shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())