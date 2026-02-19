"""
System monitoring service for Aegis Sentinel.

This module provides real-time system metrics collection including CPU, RAM,
and API latency monitoring. All metrics are collected with strict type safety
and structured logging for enterprise auditability.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

import aiohttp
import psutil
from pydantic import BaseModel, Field, validator

class MetricCollectionError(Exception):
    """Custom exception for metric collection failures."""
    pass

logger = logging.getLogger(__name__)


class SystemMetrics(BaseModel):
    """Data model for system performance metrics."""
    
    timestamp: datetime = Field(description="Timestamp of metric collection")
    cpu_percent: float = Field(ge=0.0, le=100.0, description="CPU utilization percentage")
    memory_percent: float = Field(ge=0.0, le=100.0, description="Memory utilization percentage")
    memory_used_gb: float = Field(ge=0.0, description="Memory used in gigabytes")
    memory_total_gb: float = Field(ge=0.0, description="Total memory in gigabytes")
    disk_usage_percent: float = Field(ge=0.0, le=100.0, description="Disk usage percentage")
    network_bytes_sent: int = Field(ge=0, description="Network bytes sent since last check")
    network_bytes_recv: int = Field(ge=0, description="Network bytes received since last check")
    
    @validator('memory_used_gb', 'memory_total_gb')
    def validate_memory_values(cls, v: float) -> float:
        """Ensure memory values are positive and reasonable."""
        if v < 0:
            raise ValueError("Memory values must be positive")
        if v > 10000:  # Cap at 10TB for sanity
            raise ValueError("Memory values appear unreasonably high")
        return v


class APIMetrics(BaseModel):
    """Data model for API performance metrics."""
    
    timestamp: datetime = Field(description="Timestamp of metric collection")
    endpoint: str = Field(min_length=1, description="API endpoint being monitored")
    response_time_ms: float = Field(ge=0.0, description="Response time in milliseconds")
    status_code: int = Field(ge=0, le=599, description="HTTP status code (0 for timeout/errors)")
    success: bool = Field(description="Whether the request was successful")
    error_message: Optional[str] = Field(default=None, description="Error message if request failed")


@dataclass
class MonitoringConfig:
    """Configuration for the monitoring service."""
    
    collection_interval: float = Field(default=5.0, gt=0.0, description="Interval between metric collections in seconds")
    api_endpoints: List[str] = Field(default_factory=list, description="List of API endpoints to monitor")
    enable_network_monitoring: bool = Field(default=True, description="Whether to monitor network metrics")
    enable_disk_monitoring: bool = Field(default=True, description="Whether to monitor disk metrics")


class SystemMonitor:
    """Core system monitoring service."""
    
    def __init__(self, config: Optional[MonitoringConfig] = None) -> None:
        """Initialize the system monitor with configuration."""
        self.config = config or MonitoringConfig()
        # MEMORY HARDENING: Use deque with max length for automatic memory management
        self._metrics_buffer: deque[SystemMetrics] = deque(maxlen=5000)
        self._api_metrics_buffer: deque[APIMetrics] = deque(maxlen=5000)
        self._network_counters: Dict[str, int] = {}
        self._network_lock = asyncio.Lock()  # Thread-safe protection for network counters
        self._buffer_lock = asyncio.Lock()  # Atomic buffer synchronization
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._health_check_counter = 0
        
        # API CIRCUIT BREAKER: Track API failure counts per endpoint
        self._api_failure_counts: Dict[str, int] = {}
        self._api_circuit_open: Dict[str, bool] = {}
        self._api_circuit_open_time: Dict[str, datetime] = {}
        self._api_circuit_recovery_timeout = 60.0  # 60 seconds
        
        logger.info("SystemMonitor initialized", extra={
            "collection_interval": self.config.collection_interval,
            "api_endpoints_count": len(self.config.api_endpoints),
            "network_monitoring": self.config.enable_network_monitoring,
            "disk_monitoring": self.config.enable_disk_monitoring,
            "buffer_max_size": 1000,
            "api_circuit_breaker_enabled": len(self.config.api_endpoints) > 0
        })
    
    async def start_monitoring(self) -> None:
        """Start the monitoring service."""
        if self._running:
            logger.warning("Monitoring service already running")
            return
        
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info("System monitoring started")
    
    async def stop_monitoring(self) -> None:
        """Stop the monitoring service."""
        if not self._running:
            logger.warning("Monitoring service not running")
            return
        
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("System monitoring stopped")
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop that collects metrics at regular intervals."""
        while self._running:
            try:
                # Collect system metrics with atomic buffer synchronization
                system_metrics = await self._collect_system_metrics()
                async with self._buffer_lock:
                    self._metrics_buffer.append(system_metrics)
                
                # Collect API metrics if endpoints are configured with atomic buffer synchronization
                if self.config.api_endpoints:
                    api_metrics = await self._collect_api_metrics()
                    async with self._buffer_lock:
                        self._api_metrics_buffer.extend(api_metrics)
                
                # Keep buffer size manageable with TTL (30 minutes)
                self._cleanup_old_metrics()
                
                # Health check every 5 cycles
                self._health_check_counter += 1
                if self._health_check_counter % 5 == 0:
                    await self._perform_health_check()
                
                await asyncio.sleep(self.config.collection_interval)
                
            except Exception as e:
                logger.error("Error in monitoring loop", exc_info=True, extra={
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                })
                await asyncio.sleep(self.config.collection_interval)
    
    async def _perform_health_check(self) -> None:
        """Perform internal health check and report system status."""
        try:
            # Check buffer sizes
            metrics_buffer_size = len(self._metrics_buffer)
            api_buffer_size = len(self._api_metrics_buffer)
            max_capacity = 1000
            
            # Check network lock status (if available)
            network_lock_status = "locked" if self._network_lock.locked() else "unlocked"
            
            # MEMORY HEALTH CHECK: Alert if buffers exceed 90% capacity
            metrics_usage_percent = (metrics_buffer_size / max_capacity) * 100
            api_usage_percent = (api_buffer_size / max_capacity) * 100
            
            health_check_data = {
                "metrics_buffer_size": metrics_buffer_size,
                "api_buffer_size": api_buffer_size,
                "network_lock_status": network_lock_status,
                "max_buffer_capacity": max_capacity,
                "health_check_cycle": self._health_check_counter,
                "metrics_usage_percent": round(metrics_usage_percent, 1),
                "api_usage_percent": round(api_usage_percent, 1)
            }
            
            # CRITICAL MEMORY ALERT: Buffer usage exceeds 90%
            if metrics_usage_percent > 90 or api_usage_percent > 90:
                logger.critical("Buffer memory usage critical - approaching capacity limits", extra={
                    **health_check_data,
                    "memory_alert": "CRITICAL",
                    "action_required": "Consider increasing buffer cleanup frequency or reducing collection interval"
                })
            elif metrics_usage_percent > 70 or api_usage_percent > 70:
                logger.warning("Buffer memory usage high - approaching capacity limits", extra={
                    **health_check_data,
                    "memory_alert": "WARNING",
                    "action_required": "Monitor buffer growth and cleanup effectiveness"
                })
            else:
                logger.info("Internal health check", extra=health_check_data)
            
        except Exception as e:
            logger.error("Health check failed", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
    
    async def _collect_system_metrics(self) -> SystemMetrics:
        """Collect current system performance metrics."""
        try:
            # CPU metrics
            cpu_percent = psutil.cpu_percent(interval=1.0)
            
            # Memory metrics
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_used_gb = memory.used / (1024**3)  # Convert to GB
            memory_total_gb = memory.total / (1024**3)  # Convert to GB
            
            # Disk metrics
            disk_usage_percent = 0.0
            if self.config.enable_disk_monitoring:
                disk = psutil.disk_usage('/')
                disk_usage_percent = (disk.used / disk.total) * 100
            
            # Network metrics
            network_sent = 0
            network_recv = 0
            network_sent_diff = 0
            network_recv_diff = 0
            
            if self.config.enable_network_monitoring:
                network = psutil.net_io_counters()
                network_sent = network.bytes_sent
                network_recv = network.bytes_recv
                
                # ATOMIC INTEGRITY: Complete atomic transaction within lock
                async with self._network_lock:
                    try:
                        # ATOMIC OPERATION: Read old counters, calculate differential, and write new counters in single transaction
                        if self._network_counters:
                            old_sent = self._network_counters['sent']
                            old_recv = self._network_counters['recv']
                            # CRITICAL: Calculate differential BEFORE updating counters
                            network_sent_diff = network_sent - old_sent
                            network_recv_diff = network_recv - old_recv
                        else:
                            network_sent_diff = 0
                            network_recv_diff = 0
                        
                        # ATOMIC UPDATE: Write new counters in same transaction
                        self._network_counters = {
                            'sent': network_sent,
                            'recv': network_recv
                        }
                        
                    except Exception as e:
                        # CRITICAL FAILURE: Network counter corruption detected
                        logger.critical("Network counter atomic transaction failed - SYSTEM INTEGRITY AT RISK", exc_info=True, extra={
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "network_sent": network_sent,
                            "network_recv": network_recv,
                            "previous_counters": self._network_counters
                        })
                        # Continue with zero differential to prevent data corruption
                        network_sent_diff = 0
                        network_recv_diff = 0
            
            metrics = SystemMetrics(
                timestamp=datetime.now(),
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                memory_used_gb=memory_used_gb,
                memory_total_gb=memory_total_gb,
                disk_usage_percent=disk_usage_percent,
                network_bytes_sent=network_sent_diff,
                network_bytes_recv=network_recv_diff
            )
            
            logger.debug("System metrics collected", extra={
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "memory_used_gb": round(memory_used_gb, 2),
                "disk_usage_percent": round(disk_usage_percent, 2)
            })
            
            return metrics
            
        except Exception as e:
            logger.error("Failed to collect system metrics", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise
    
    async def _collect_api_metrics(self) -> List[APIMetrics]:
        """Collect API performance metrics for configured endpoints."""
        
        metrics = []
        
        for endpoint in self.config.api_endpoints:
            # API CIRCUIT BREAKER: Check if circuit is open for this endpoint
            if self._api_circuit_open.get(endpoint, False):
                # Check if recovery timeout has passed
                open_time = self._api_circuit_open_time.get(endpoint)
                if open_time and (datetime.now() - open_time).total_seconds() < self._api_circuit_recovery_timeout:
                    logger.warning("API circuit breaker open - skipping endpoint", extra={
                        "endpoint": endpoint,
                        "circuit_open_time": open_time.isoformat(),
                        "recovery_timeout": self._api_circuit_recovery_timeout
                    })
                    continue
                else:
                    # Recovery timeout passed, reset circuit
                    self._api_circuit_open[endpoint] = False
                    self._api_failure_counts[endpoint] = 0
                    logger.info("API circuit breaker reset - resuming monitoring", extra={
                        "endpoint": endpoint
                    })
            
            try:
                start_time = time.time()
                
                async with aiohttp.ClientSession() as session:
                    response = await session.get(endpoint, timeout=aiohttp.ClientTimeout(total=10))
                    async with response:
                        response_time = (time.time() - start_time) * 1000  # Convert to milliseconds
                        
                        api_metric = APIMetrics(
                            timestamp=datetime.now(),
                            endpoint=endpoint,
                            response_time_ms=response_time,
                            status_code=response.status,
                            success=200 <= response.status < 300
                        )
                        
                        if not api_metric.success:
                            api_metric.error_message = f"HTTP {response.status}"
                
                # API CIRCUIT BREAKER: Reset failure count on success
                self._api_failure_counts[endpoint] = 0
                
                metrics.append(api_metric)
                
                logger.debug("API metrics collected", extra={
                    "endpoint": endpoint,
                    "response_time_ms": round(response_time, 2),
                    "status_code": response.status,
                    "success": api_metric.success
                })
                
            except asyncio.TimeoutError:
                api_metric = APIMetrics(
                    timestamp=datetime.now(),
                    endpoint=endpoint,
                    response_time_ms=10000.0,  # Timeout threshold
                    status_code=0,
                    success=False,
                    error_message="Request timeout"
                )
                metrics.append(api_metric)
                
                # API CIRCUIT BREAKER: Increment failure count
                self._api_failure_counts[endpoint] = self._api_failure_counts.get(endpoint, 0) + 1
                
                logger.warning("API endpoint timeout", extra={
                    "endpoint": endpoint,
                    "timeout_ms": 10000,
                    "failure_count": self._api_failure_counts[endpoint]
                })
                
                # API CIRCUIT BREAKER: Open circuit if 3 consecutive failures
                if self._api_failure_counts[endpoint] >= 3:
                    self._api_circuit_open[endpoint] = True
                    self._api_circuit_open_time[endpoint] = datetime.now()
                    logger.critical("API circuit breaker opened - RESOURCE EXHAUSTION PROTECTION ACTIVATED", extra={
                        "endpoint": endpoint,
                        "failure_count": self._api_failure_counts[endpoint],
                        "recovery_timeout": self._api_circuit_recovery_timeout
                    })
                
            except Exception as e:
                api_metric = APIMetrics(
                    timestamp=datetime.now(),
                    endpoint=endpoint,
                    response_time_ms=0.0,
                    status_code=0,
                    success=False,
                    error_message=str(e)
                )
                metrics.append(api_metric)
                
                # API CIRCUIT BREAKER: Increment failure count
                self._api_failure_counts[endpoint] = self._api_failure_counts.get(endpoint, 0) + 1
                
                logger.error("Failed to collect API metrics", exc_info=True, extra={
                    "endpoint": endpoint,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "failure_count": self._api_failure_counts[endpoint]
                })
                
                # API CIRCUIT BREAKER: Open circuit if 3 consecutive failures
                if self._api_failure_counts[endpoint] >= 3:
                    self._api_circuit_open[endpoint] = True
                    self._api_circuit_open_time[endpoint] = datetime.now()
                    logger.critical("API circuit breaker opened - RESOURCE EXHAUSTION PROTECTION ACTIVATED", extra={
                        "endpoint": endpoint,
                        "failure_count": self._api_failure_counts[endpoint],
                        "recovery_timeout": self._api_circuit_recovery_timeout
                    })
        
        return metrics
    
    def _cleanup_old_metrics(self) -> None:
        """Clean up old metrics from buffers based on TTL (30 minutes) using efficient deque operations."""
        cutoff_time = datetime.now() - timedelta(minutes=30)
        
        # ATOMIC BUFFER CLEANUP: Robust cleanup with exception handling for graceful degradation
        try:
            # TRUE DEQUE MEMORY MANAGEMENT: Use popleft() to maintain O(1) complexity
            while self._metrics_buffer and self._metrics_buffer[0].timestamp < cutoff_time:
                self._metrics_buffer.popleft()
        except Exception as e:
            # GRACEFUL DEGRADATION: Log critical error but continue operating
            logger.critical("Failed to cleanup system metrics buffer", exc_info=True, extra={
                "buffer_size": len(self._metrics_buffer),
                "cutoff_time": cutoff_time.isoformat(),
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
        finally:
            # HARD BUFFER LIMITS: Force cleanup to maintain 20% safety margin
            if len(self._metrics_buffer) > 5000:  # Exceeds hard limit
                logger.critical("System metrics buffer exceeded hard limit - forcing cleanup", extra={
                    "buffer_size": len(self._metrics_buffer),
                    "hard_limit": 5000,
                    "safety_margin": "20%"
                })
                # Force reduce to 4000 (20% safety margin below 5000)
                while len(self._metrics_buffer) > 4000:
                    self._metrics_buffer.popleft()
            elif len(self._metrics_buffer) > 4500:  # 90% of maxlen=5000
                logger.warning("System metrics buffer approaching capacity limit", extra={
                    "buffer_size": len(self._metrics_buffer),
                    "maxlen": 5000
                })
        
        try:
            # TRUE DEQUE MEMORY MANAGEMENT: Use popleft() for API metrics as well
            while self._api_metrics_buffer and self._api_metrics_buffer[0].timestamp < cutoff_time:
                self._api_metrics_buffer.popleft()
        except Exception as e:
            # GRACEFUL DEGRADATION: Log critical error but continue operating
            logger.critical("Failed to cleanup API metrics buffer", exc_info=True, extra={
                "buffer_size": len(self._api_metrics_buffer),
                "cutoff_time": cutoff_time.isoformat(),
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
        finally:
            # HARD BUFFER LIMITS: Force cleanup to maintain 20% safety margin
            if len(self._api_metrics_buffer) > 5000:  # Exceeds hard limit
                logger.critical("API metrics buffer exceeded hard limit - forcing cleanup", extra={
                    "buffer_size": len(self._api_metrics_buffer),
                    "hard_limit": 5000,
                    "safety_margin": "20%"
                })
                # Force reduce to 4000 (20% safety margin below 5000)
                while len(self._api_metrics_buffer) > 4000:
                    self._api_metrics_buffer.popleft()
            elif len(self._api_metrics_buffer) > 4500:  # 90% of maxlen=5000
                logger.warning("API metrics buffer approaching capacity limit", extra={
                    "buffer_size": len(self._api_metrics_buffer),
                    "maxlen": 5000
                })
    
    def get_latest_metrics(self, limit: int = 100) -> List[SystemMetrics]:
        """Get the latest system metrics from the buffer."""
        return list(self._metrics_buffer)[-limit:] if self._metrics_buffer else []
    
    def get_latest_api_metrics(self, limit: int = 100) -> List[APIMetrics]:
        """Get the latest API metrics from the buffer."""
        return list(self._api_metrics_buffer)[-limit:] if self._api_metrics_buffer else []
    
    def get_metrics_summary(self) -> Dict[str, Union[float, int, str]]:
        """Get a summary of current system state."""
        if not self._metrics_buffer:
            return {}
        
        latest = self._metrics_buffer[-1]
        return {
            "cpu_percent": latest.cpu_percent,
            "memory_percent": latest.memory_percent,
            "memory_used_gb": latest.memory_used_gb,
            "disk_usage_percent": latest.disk_usage_percent,
            "network_bytes_sent": latest.network_bytes_sent,
            "network_bytes_recv": latest.network_bytes_recv,
            "timestamp": latest.timestamp.isoformat()
        }