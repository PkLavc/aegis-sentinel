"""
Recovery and healing service for Aegis Sentinel.

This module implements automated recovery actions to remediate detected
anomalies. The recovery engine can restart docker containers, flush caches,
re-route traffic, and perform other automated fixes to restore system health.
"""

import asyncio
import logging
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Union

import docker
import docker.errors
from pydantic import BaseModel, Field, validator

from .detector import AnomalyResult

logger = logging.getLogger(__name__)


class RecoveryAction(BaseModel):
    """Data model for a recovery action."""
    
    action_id: str = Field(description="Unique identifier for the recovery action")
    action_type: str = Field(description="Type of recovery action to perform")
    target: str = Field(description="Target of the recovery action")
    parameters: Dict[str, Union[str, int, float, bool]] = Field(default_factory=dict, description="Parameters for the action")
    priority: int = Field(ge=1, le=10, description="Priority of the action (1-10, higher is more critical)")
    max_retries: int = Field(default=3, ge=0, le=10, description="Maximum number of retry attempts")
    retry_delay: float = Field(default=5.0, gt=0.0, description="Delay between retry attempts in seconds")
    timeout: float = Field(default=60.0, gt=0.0, description="Timeout for the action in seconds")
    
    @validator('action_type')
    def validate_action_type(cls, v: str) -> str:
        """Validate that the action type is supported."""
        supported_actions = [
            'restart_container', 'flush_cache', 'scale_service', 
            'restart_service', 'clear_logs', 'kill_process'
        ]
        if v not in supported_actions:
            raise ValueError(f"Unsupported action type: {v}")
        return v


class RecoveryResult(BaseModel):
    """Data model for recovery action results."""
    
    action_id: str = Field(description="Identifier of the recovery action")
    timestamp: datetime = Field(description="Timestamp of the recovery action")
    success: bool = Field(description="Whether the recovery action was successful")
    action_type: str = Field(description="Type of recovery action performed")
    target: str = Field(description="Target of the recovery action")
    execution_time: float = Field(ge=0.0, description="Time taken to execute the action in seconds")
    error_message: Optional[str] = Field(default=None, description="Error message if the action failed")
    retry_count: int = Field(default=0, ge=0, description="Number of retry attempts made")
    final_state: str = Field(description="Final state after recovery action")


@dataclass
class RecoveryConfig:
    """Configuration for the recovery service."""
    
    enable_docker_recovery: bool = Field(default=True, description="Whether to enable Docker container recovery")
    enable_cache_recovery: bool = Field(default=True, description="Whether to enable cache flushing recovery")
    enable_service_recovery: bool = Field(default=True, description="Whether to enable service restart recovery")
    max_concurrent_actions: int = Field(default=3, ge=1, le=10, description="Maximum number of concurrent recovery actions")
    action_timeout: float = Field(default=120.0, gt=0.0, description="Default timeout for recovery actions in seconds")
    retry_enabled: bool = Field(default=True, description="Whether to enable automatic retries for failed actions")


class RecoveryActionHandler(ABC):
    """Abstract base class for recovery action handlers."""
    
    @abstractmethod
    async def execute(self, action: RecoveryAction) -> RecoveryResult:
        """Execute the recovery action."""
        pass
    
    @abstractmethod
    def can_handle(self, action_type: str) -> bool:
        """Check if this handler can execute the given action type."""
        pass


class DockerRecoveryHandler(RecoveryActionHandler):
    """Handler for Docker container recovery actions."""
    
    def __init__(self, config: RecoveryConfig) -> None:
        """Initialize the Docker recovery handler."""
        self.config = config
        self._docker_client = None
        
        try:
            self._docker_client = docker.from_env()
            logger.info("Docker recovery handler initialized")
        except docker.errors.DockerException as e:
            logger.error("Failed to initialize Docker client", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            # Don't raise exception - allow system to continue without Docker recovery
            self._docker_client = None
    
    def can_handle(self, action_type: str) -> bool:
        """Check if this handler can execute the given action type."""
        return action_type == 'restart_container' and self._docker_client is not None
    
    async def execute(self, action: RecoveryAction) -> RecoveryResult:
        """Execute Docker container recovery action."""
        if not self._docker_client:
            return RecoveryResult(
                action_id=action.action_id,
                timestamp=datetime.now(),
                success=False,
                action_type=action.action_type,
                target=action.target,
                execution_time=0.0,
                error_message="Docker client not available",
                retry_count=0,
                final_state="failed"
            )
        
        start_time = time.time()
        retry_count = 0
        
        while retry_count <= action.max_retries:
            try:
                # Get container
                container = self._docker_client.containers.get(action.target)
                
                # Restart container
                container.restart(timeout=int(action.timeout))
                
                # Wait for container to be healthy
                await self._wait_for_container_health(container, action.timeout)
                
                execution_time = time.time() - start_time
                
                result = RecoveryResult(
                    action_id=action.action_id,
                    timestamp=datetime.now(),
                    success=True,
                    action_type=action.action_type,
                    target=action.target,
                    execution_time=execution_time,
                    retry_count=retry_count,
                    final_state="healthy"
                )
                
                logger.info("Docker container recovery successful", extra={
                    "container": action.target,
                    "execution_time": execution_time,
                    "retry_count": retry_count
                })
                
                return result
                
            except docker.errors.NotFound:
                execution_time = time.time() - start_time
                error_msg = f"Container {action.target} not found"
                
                logger.error("Docker container not found", extra={
                    "container": action.target,
                    "error_message": error_msg
                })
                
                return RecoveryResult(
                    action_id=action.action_id,
                    timestamp=datetime.now(),
                    success=False,
                    action_type=action.action_type,
                    target=action.target,
                    execution_time=execution_time,
                    error_message=error_msg,
                    retry_count=retry_count,
                    final_state="not_found"
                )
                
            except Exception as e:
                retry_count += 1
                execution_time = time.time() - start_time
                
                if retry_count > action.max_retries:
                    error_msg = f"Failed after {action.max_retries} retries: {str(e)}"
                    
                    logger.error("Docker container recovery failed after retries", exc_info=True, extra={
                        "container": action.target,
                        "retry_count": retry_count - 1,
                        "error_message": str(e)
                    })
                    
                    return RecoveryResult(
                        action_id=action.action_id,
                        timestamp=datetime.now(),
                        success=False,
                        action_type=action.action_type,
                        target=action.target,
                        execution_time=execution_time,
                        error_message=error_msg,
                        retry_count=retry_count - 1,
                        final_state="failed"
                    )
                
                logger.warning("Docker container recovery retry", extra={
                    "container": action.target,
                    "retry_count": retry_count,
                    "error_message": str(e)
                })
                
                await asyncio.sleep(action.retry_delay)
        
        # This should never be reached, but return a failure result as fallback
        return RecoveryResult(
            action_id=action.action_id,
            timestamp=datetime.now(),
            success=False,
            action_type=action.action_type,
            target=action.target,
            execution_time=time.time() - start_time,
            error_message="Unexpected end of execution",
            retry_count=retry_count,
            final_state="failed"
        )
    
    async def _wait_for_container_health(self, container, timeout: float) -> None:
        """Wait for container to reach healthy state."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            container.reload()
            health_status = container.attrs.get('State', {}).get('Health', {}).get('Status')
            
            if health_status == 'healthy':
                return
            elif health_status in ['unhealthy', 'starting']:
                await asyncio.sleep(1.0)
                continue
            else:
                # No health check configured, consider it healthy
                return
        
        raise TimeoutError(f"Container {container.name} did not become healthy within {timeout} seconds")


class CacheRecoveryHandler(RecoveryActionHandler):
    """Handler for cache flushing recovery actions."""
    
    def __init__(self, config: RecoveryConfig) -> None:
        """Initialize the cache recovery handler."""
        self.config = config
        logger.info("Cache recovery handler initialized")
    
    def can_handle(self, action_type: str) -> bool:
        """Check if this handler can execute the given action type."""
        return action_type == 'flush_cache'
    
    async def execute(self, action: RecoveryAction) -> RecoveryResult:
        """Execute cache flushing recovery action."""
        start_time = time.time()
        
        try:
            cache_type = action.parameters.get('cache_type', 'redis')
            cache_host = action.parameters.get('host', 'localhost')
            cache_port = action.parameters.get('port', 6379)
            
            # Ensure types are correct
            cache_host = str(cache_host) if cache_host is not None else 'localhost'
            cache_port = int(cache_port) if cache_port is not None else 6379
            
            if cache_type == 'redis':
                await self._flush_redis_cache(cache_host, cache_port, action.timeout)
            elif cache_type == 'memcached':
                await self._flush_memcached_cache(cache_host, cache_port, action.timeout)
            else:
                raise ValueError(f"Unsupported cache type: {cache_type}")
            
            execution_time = time.time() - start_time
            
            result = RecoveryResult(
                action_id=action.action_id,
                timestamp=datetime.now(),
                success=True,
                action_type=action.action_type,
                target=action.target,
                execution_time=execution_time,
                retry_count=0,
                final_state="cleared"
            )
            
            logger.info("Cache flush successful", extra={
                "cache_type": cache_type,
                "cache_host": cache_host,
                "cache_port": cache_port,
                "execution_time": execution_time
            })
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            
            logger.error("Cache flush failed", exc_info=True, extra={
                "cache_type": action.parameters.get('cache_type', 'unknown'),
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            
            return RecoveryResult(
                action_id=action.action_id,
                timestamp=datetime.now(),
                success=False,
                action_type=action.action_type,
                target=action.target,
                execution_time=execution_time,
                error_message=str(e),
                retry_count=0,
                final_state="failed"
            )
    
    async def _flush_redis_cache(self, host: str, port: int, timeout: float) -> None:
        """Flush Redis cache."""
        import aioredis
        
        try:
            redis = await aioredis.from_url(f"redis://{host}:{port}", encoding="utf-8", decode_responses=True)
            await redis.flushall()
            await redis.close()
        except Exception as e:
            raise RuntimeError(f"Failed to flush Redis cache: {str(e)}")
    
    async def _flush_memcached_cache(self, host: str, port: int, timeout: float) -> None:
        """Flush Memcached cache."""
        try:
            # Use system command to flush memcached
            cmd = f"echo 'flush_all' | nc {host} {port}"
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            
            if process.returncode != 0:
                raise RuntimeError(f"Memcached flush command failed: {stderr.decode()}")
                
        except asyncio.TimeoutError:
            raise TimeoutError(f"Memcached flush timed out after {timeout} seconds")
        except Exception as e:
            raise RuntimeError(f"Failed to flush Memcached cache: {str(e)}")


class ServiceRecoveryHandler(RecoveryActionHandler):
    """Handler for service restart recovery actions."""
    
    def __init__(self, config: RecoveryConfig) -> None:
        """Initialize the service recovery handler."""
        self.config = config
        logger.info("Service recovery handler initialized")
    
    def can_handle(self, action_type: str) -> bool:
        """Check if this handler can execute the given action type."""
        return action_type in ['restart_service', 'kill_process']
    
    async def execute(self, action: RecoveryAction) -> RecoveryResult:
        """Execute service restart or process kill recovery action."""
        start_time = time.time()
        
        try:
            if action.action_type == 'restart_service':
                await self._restart_system_service(action.target, action.timeout)
            elif action.action_type == 'kill_process':
                signal = action.parameters.get('signal', 'TERM')
                signal = str(signal) if signal is not None else 'TERM'
                await self._kill_process(action.target, signal)
            
            execution_time = time.time() - start_time
            
            result = RecoveryResult(
                action_id=action.action_id,
                timestamp=datetime.now(),
                success=True,
                action_type=action.action_type,
                target=action.target,
                execution_time=execution_time,
                retry_count=0,
                final_state="restarted"
            )
            
            logger.info("Service recovery successful", extra={
                "action_type": action.action_type,
                "target": action.target,
                "execution_time": execution_time
            })
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            
            logger.error("Service recovery failed", exc_info=True, extra={
                "action_type": action.action_type,
                "target": action.target,
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            
            return RecoveryResult(
                action_id=action.action_id,
                timestamp=datetime.now(),
                success=False,
                action_type=action.action_type,
                target=action.target,
                execution_time=execution_time,
                error_message=str(e),
                retry_count=0,
                final_state="failed"
            )
    
    async def _restart_system_service(self, service_name: str, timeout: float) -> None:
        """Restart a system service using systemctl."""
        try:
            # Check if service exists
            check_cmd = ['systemctl', 'is-active', service_name]
            check_process = await asyncio.create_subprocess_exec(
                *check_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await check_process.communicate()
            
            if check_process.returncode != 0:
                raise RuntimeError(f"Service {service_name} not found or not active")
            
            # Restart service
            restart_cmd = ['systemctl', 'restart', service_name]
            restart_process = await asyncio.create_subprocess_exec(
                *restart_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(restart_process.communicate(), timeout=timeout)
            
            if restart_process.returncode != 0:
                raise RuntimeError(f"Failed to restart service: {stderr.decode()}")
            
            # Wait for service to be active again
            await self._wait_for_service_active(service_name, timeout)
            
        except asyncio.TimeoutError:
            raise TimeoutError(f"Service restart timed out after {timeout} seconds")
        except Exception as e:
            raise RuntimeError(f"Failed to restart service {service_name}: {str(e)}")
    
    async def _kill_process(self, process_name: str, signal: str = 'TERM') -> None:
        """Kill a process by name."""
        try:
            # Find process ID
            pgrep_cmd = ['pgrep', process_name]
            pgrep_process = await asyncio.create_subprocess_exec(
                *pgrep_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await pgrep_process.communicate()
            
            if pgrep_process.returncode != 0:
                raise RuntimeError(f"Process {process_name} not found")
            
            # Kill process
            pkill_cmd = ['pkill', '-f', f'-{signal}', process_name]
            pkill_process = await asyncio.create_subprocess_exec(
                *pkill_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await pkill_process.communicate()
            
            if pkill_process.returncode != 0:
                raise RuntimeError(f"Failed to kill process: {stderr.decode()}")
                
        except Exception as e:
            raise RuntimeError(f"Failed to kill process {process_name}: {str(e)}")
    
    async def _wait_for_service_active(self, service_name: str, timeout: float) -> None:
        """Wait for service to become active."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            check_cmd = ['systemctl', 'is-active', service_name]
            check_process = await asyncio.create_subprocess_exec(
                *check_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await check_process.communicate()
            
            if check_process.returncode == 0:
                return
            
            await asyncio.sleep(1.0)
        
        raise TimeoutError(f"Service {service_name} did not become active within {timeout} seconds")


class RecoveryEngine:
    """Main recovery engine that orchestrates automated healing actions."""
    
    def __init__(self, config: Optional[RecoveryConfig] = None) -> None:
        """Initialize the recovery engine."""
        self.config = config or RecoveryConfig()
        self._handlers: List[RecoveryActionHandler] = []
        self._active_actions: Dict[str, asyncio.Task] = {}
        
        # Initialize handlers based on configuration
        if self.config.enable_docker_recovery:
            self._handlers.append(DockerRecoveryHandler(self.config))
        
        if self.config.enable_cache_recovery:
            self._handlers.append(CacheRecoveryHandler(self.config))
        
        if self.config.enable_service_recovery:
            self._handlers.append(ServiceRecoveryHandler(self.config))
        
        logger.info("Recovery engine initialized", extra={
            "handlers_count": len(self._handlers),
            "max_concurrent_actions": self.config.max_concurrent_actions
        })
    
    async def trigger_recovery(self, anomaly: AnomalyResult) -> List[RecoveryResult]:
        """Trigger recovery actions based on detected anomaly."""
        if not anomaly.anomaly_detected:
            logger.debug("No anomaly detected, skipping recovery", extra={
                "anomaly_type": anomaly.metric_type,
                "severity": anomaly.severity_level
            })
            return []
        
        actions = self._generate_recovery_actions(anomaly)
        results = []
        
        if not actions:
            logger.warning("No recovery actions available for anomaly", extra={
                "anomaly_type": anomaly.metric_type,
                "severity": anomaly.severity_level
            })
            return results
        
        # Execute actions with concurrency control
        semaphore = asyncio.Semaphore(self.config.max_concurrent_actions)
        
        async def execute_with_semaphore(action: RecoveryAction) -> RecoveryResult:
            async with semaphore:
                try:
                    return await self._execute_action(action)
                except Exception as e:
                    # Ensure semaphore is released even if action fails
                    raise e
        
        tasks = [execute_with_semaphore(action) for action in actions]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Recovery action failed with exception", exc_info=True, extra={
                    "action_id": actions[i].action_id,
                    "error_type": type(result).__name__,
                    "error_message": str(result)
                })
                
                final_results.append(RecoveryResult(
                    action_id=actions[i].action_id,
                    timestamp=datetime.now(),
                    success=False,
                    action_type=actions[i].action_type,
                    target=actions[i].target,
                    execution_time=0.0,
                    error_message=str(result),
                    retry_count=0,
                    final_state="failed"
                ))
            else:
                final_results.append(result)
        
        logger.info("Recovery actions completed", extra={
            "anomaly_type": anomaly.metric_type,
            "actions_count": len(actions),
            "successful_actions": sum(1 for r in final_results if r.success)
        })
        
        return final_results
    
    def _generate_recovery_actions(self, anomaly: AnomalyResult) -> List[RecoveryAction]:
        """Generate appropriate recovery actions based on anomaly type and severity."""
        actions = []
        
        try:
            if anomaly.metric_type == "system_isolation_forest" or anomaly.metric_type == "system_statistical":
                # System-level anomalies
                if anomaly.severity_level in ["critical", "high"]:
                    # Restart high-impact containers
                    containers = self._get_high_impact_containers()
                    for container in containers[:3]:  # Limit to 3 containers
                        actions.append(RecoveryAction(
                            action_id=f"restart_container_{container}_{int(time.time())}",
                            action_type="restart_container",
                            target=container,
                            priority=9 if anomaly.severity_level == "critical" else 7,
                            max_retries=2,
                            retry_delay=10.0,
                            timeout=30.0
                        ))
                
                if anomaly.severity_level in ["high", "medium"]:
                    # Flush caches
                    actions.append(RecoveryAction(
                        action_id=f"flush_cache_{int(time.time())}",
                        action_type="flush_cache",
                        target="system_cache",
                        parameters={"cache_type": "redis", "host": "localhost", "port": 6379},
                        priority=6,
                        max_retries=1,
                        retry_delay=5.0,
                        timeout=10.0
                    ))
            
            elif anomaly.metric_type == "api_response_time":
                # API performance issues
                endpoint = anomaly.metric_values.get("endpoint", "unknown")
                endpoint_str = str(endpoint) if endpoint is not None else "unknown"
                actions.append(RecoveryAction(
                    action_id=f"restart_service_{endpoint_str}_{int(time.time())}",
                    action_type="restart_service",
                    target=f"api-{endpoint_str.replace('/', '-')}",
                    priority=8,
                    max_retries=2,
                    retry_delay=5.0,
                    timeout=30.0
                ))
            
            elif anomaly.metric_type == "api_success_rate":
                # API availability issues
                endpoint = anomaly.metric_values.get("endpoint", "unknown")
                endpoint_str = str(endpoint) if endpoint is not None else "unknown"
                actions.append(RecoveryAction(
                    action_id=f"flush_cache_{endpoint_str}_{int(time.time())}",
                    action_type="flush_cache",
                    target=f"cache-{endpoint_str.replace('/', '-')}",
                    parameters={"cache_type": "redis", "host": "localhost", "port": 6379},
                    priority=7,
                    max_retries=1,
                    retry_delay=3.0,
                    timeout=10.0
                ))
        
        except Exception as e:
            logger.error("Failed to generate recovery actions", exc_info=True, extra={
                "anomaly_type": anomaly.metric_type,
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
        
        # Sort actions by priority
        actions.sort(key=lambda a: a.priority, reverse=True)
        
        return actions
    
    async def _execute_action(self, action: RecoveryAction) -> RecoveryResult:
        """Execute a single recovery action."""
        # Find appropriate handler
        handler = None
        for h in self._handlers:
            if h.can_handle(action.action_type):
                handler = h
                break
        
        if not handler:
            return RecoveryResult(
                action_id=action.action_id,
                timestamp=datetime.now(),
                success=False,
                action_type=action.action_type,
                target=action.target,
                execution_time=0.0,
                error_message=f"No handler available for action type: {action.action_type}",
                retry_count=0,
                final_state="no_handler"
            )
        
        # Execute with timeout
        try:
            result = await asyncio.wait_for(
                handler.execute(action),
                timeout=action.timeout
            )
            return result
        except asyncio.TimeoutError:
            return RecoveryResult(
                action_id=action.action_id,
                timestamp=datetime.now(),
                success=False,
                action_type=action.action_type,
                target=action.target,
                execution_time=action.timeout,
                error_message=f"Action timed out after {action.timeout} seconds",
                retry_count=0,
                final_state="timeout"
            )
    
    def _get_high_impact_containers(self) -> List[str]:
        """Get list of high-impact containers that should be prioritized for recovery."""
        try:
            if not self._handlers:
                return []
            
            docker_handler = next((h for h in self._handlers if isinstance(h, DockerRecoveryHandler)), None)
            if not docker_handler or not docker_handler._docker_client:
                return []
            
            containers = docker_handler._docker_client.containers.list()
            high_impact_containers = []
            
            for container in containers:
                # Check if container is critical (has restart policy or is part of a critical service)
                restart_policy = container.attrs.get('HostConfig', {}).get('RestartPolicy', {}).get('Name')
                labels = container.labels
                
                if (restart_policy in ['always', 'unless-stopped'] or 
                    labels.get('aegis-sentinel.critical', 'false').lower() == 'true'):
                    high_impact_containers.append(container.name)
            
            return high_impact_containers
            
        except Exception as e:
            logger.error("Failed to get high-impact containers", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            return []