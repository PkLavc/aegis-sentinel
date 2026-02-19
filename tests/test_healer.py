"""
Tests for the recovery and healing service.

This module tests the automated recovery actions including Docker container
recovery, cache flushing, and service restart functionality.
"""

import asyncio
import logging
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from src.healer import (
    CacheRecoveryHandler, DockerRecoveryHandler, RecoveryAction, 
    RecoveryConfig, RecoveryEngine, RecoveryResult, ServiceRecoveryHandler
)


class TestRecoveryAction:
    """Test cases for RecoveryAction data model."""
    
    def test_recovery_action_creation(self):
        """Test creating a valid recovery action."""
        action = RecoveryAction(
            action_id="test_action_1",
            action_type="restart_container",
            target="web-server",
            parameters={"timeout": 30},
            priority=8,
            max_retries=3,
            retry_delay=5.0,
            timeout=60.0
        )
        
        assert action.action_id == "test_action_1"
        assert action.action_type == "restart_container"
        assert action.target == "web-server"
        assert action.priority == 8
        assert action.max_retries == 3
        assert action.retry_delay == 5.0
        assert action.timeout == 60.0
    
    def test_recovery_action_validation(self):
        """Test recovery action validation."""
        # Test invalid action type
        with pytest.raises(ValueError, match="Unsupported action type"):
            RecoveryAction(
                action_id="test",
                action_type="invalid_action",
                target="test",
                priority=5
            )
        
        # Test invalid priority
        with pytest.raises(ValueError, match="Input should be greater than or equal to 1"):
            RecoveryAction(
                action_id="test",
                action_type="restart_container",
                target="test",
                priority=0
            )
        
        # Test invalid max_retries
        with pytest.raises(ValueError, match="Input should be less than or equal to 10"):
            RecoveryAction(
                action_id="test",
                action_type="restart_container",
                target="test",
                priority=5,
                max_retries=15
            )


class TestRecoveryResult:
    """Test cases for RecoveryResult data model."""
    
    def test_recovery_result_creation(self):
        """Test creating a valid recovery result."""
        result = RecoveryResult(
            action_id="test_action_1",
            timestamp=datetime.now(),
            success=True,
            action_type="restart_container",
            target="web-server",
            execution_time=2.5,
            retry_count=1,
            final_state="healthy"
        )
        
        assert result.action_id == "test_action_1"
        assert result.success is True
        assert result.action_type == "restart_container"
        assert result.target == "web-server"
        assert result.execution_time == 2.5
        assert result.retry_count == 1
        assert result.final_state == "healthy"


class TestDockerRecoveryHandler:
    """Test cases for Docker container recovery actions."""
    
    @pytest.fixture
    def config(self):
        """Create a recovery config for testing."""
        return RecoveryConfig(
            enable_docker_recovery=True,
            max_concurrent_actions=3,
            action_timeout=120.0
        )
    
    @patch('docker.from_env')
    def test_docker_handler_initialization_success(self, mock_docker_client, config):
        """Test Docker handler initialization when Docker is available."""
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client
        
        handler = DockerRecoveryHandler(config)
        
        assert handler._docker_client is not None
        assert handler.config == config
        mock_docker_client.assert_called_once()
    
    @patch('docker.from_env')
    def test_docker_handler_initialization_failure(self, mock_docker_client, config):
        """Test Docker handler initialization when Docker is unavailable."""
        mock_docker_client.side_effect = Exception("Docker not available")
        
        handler = DockerRecoveryHandler(config)
        
        assert handler._docker_client is None
        assert handler.config == config
        assert handler._monitor_only_mode is True
    
    @patch('docker.from_env')
    def test_can_handle_docker_actions(self, mock_docker_client, config):
        """Test that Docker handler can handle restart_container actions."""
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client
        
        handler = DockerRecoveryHandler(config)
        
        assert handler.can_handle('restart_container') is True
        assert handler.can_handle('flush_cache') is False
        assert handler.can_handle('restart_service') is False
    
    @patch('docker.from_env')
    def test_can_handle_when_docker_unavailable(self, mock_docker_client, config):
        """Test that Docker handler cannot handle actions when Docker is unavailable."""
        mock_docker_client.side_effect = Exception("Docker not available")
        
        handler = DockerRecoveryHandler(config)
        
        assert handler.can_handle('restart_container') is False
    
    @patch('docker.from_env')
    @patch('src.healer.DockerRecoveryHandler._wait_for_container_health')
    async def test_execute_restart_container_success(self, mock_wait_health, mock_docker_client, config):
        """Test successful container restart."""
        # Setup mocks
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.name = "test-container"
        mock_client.containers.get.return_value = mock_container
        mock_docker_client.return_value = mock_client
        
        handler = DockerRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_restart_1",
            action_type="restart_container",
            target="test-container",
            timeout=30.0
        )
        
        # Execute
        result = await handler.execute(action)
        
        # Verify
        assert result.success is True
        assert result.action_type == "restart_container"
        assert result.target == "test-container"
        assert result.execution_time >= 0
        assert result.retry_count == 0
        assert result.final_state == "healthy"
        
        mock_container.restart.assert_called_once_with(timeout=30)
        mock_wait_health.assert_called_once_with(mock_container, 30.0)
    
    @patch('docker.from_env')
    async def test_execute_container_not_found(self, mock_docker_client, config):
        """Test handling when container is not found."""
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = Exception("Container not found")
        mock_docker_client.return_value = mock_client
        
        handler = DockerRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_restart_1",
            action_type="restart_container",
            target="nonexistent-container",
            timeout=30.0
        )
        
        result = await handler.execute(action)
        
        assert result.success is False
        assert result.error_message == "Container nonexistent-container not found"
        assert result.final_state == "not_found"
    
    @patch('docker.from_env')
    @patch('src.healer.DockerRecoveryHandler._wait_for_container_health')
    async def test_execute_with_retries(self, mock_wait_health, mock_docker_client, config):
        """Test container restart with retries."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker_client.return_value = mock_client
        
        # First call fails, second succeeds
        mock_container.restart.side_effect = [
            Exception("Restart failed"),
            None  # Success on retry
        ]
        
        handler = DockerRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_restart_1",
            action_type="restart_container",
            target="test-container",
            timeout=30.0,
            max_retries=2,
            retry_delay=1.0
        )
        
        result = await handler.execute(action)
        
        assert result.success is True
        assert result.retry_count == 1
        assert mock_container.restart.call_count == 2


class TestCacheRecoveryHandler:
    """Test cases for cache flushing recovery actions."""
    
    @pytest.fixture
    def config(self):
        """Create a recovery config for testing."""
        return RecoveryConfig(
            enable_cache_recovery=True,
            action_timeout=60.0
        )
    
    def test_cache_handler_initialization(self, config):
        """Test cache handler initialization."""
        handler = CacheRecoveryHandler(config)
        
        assert handler.config == config
    
    def test_can_handle_cache_actions(self, config):
        """Test that cache handler can handle flush_cache actions."""
        handler = CacheRecoveryHandler(config)
        
        assert handler.can_handle('flush_cache') is True
        assert handler.can_handle('restart_container') is False
        assert handler.can_handle('restart_service') is False
    
    @patch('aioredis.from_url')
    async def test_execute_redis_flush_success(self, mock_redis, config):
        """Test successful Redis cache flush."""
        mock_redis_instance = AsyncMock()
        mock_redis.return_value = mock_redis_instance
        
        handler = CacheRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_flush_1",
            action_type="flush_cache",
            target="redis-cache",
            parameters={
                "cache_type": "redis",
                "host": "localhost",
                "port": 6379
            },
            priority=6,
            timeout=10.0
        )
        
        result = await handler.execute(action)
        
        assert result.success is True
        assert result.action_type == "flush_cache"
        assert result.target == "redis-cache"
        assert result.execution_time >= 0
        
        mock_redis_instance.flushall.assert_called_once()
        mock_redis_instance.close.assert_called_once()
    
    @patch('asyncio.create_subprocess_shell')
    async def test_execute_memcached_flush_success(self, mock_subprocess, config):
        """Test successful Memcached cache flush."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b"OK", b"")
        mock_subprocess.return_value = mock_process
        
        handler = CacheRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_flush_2",
            action_type="flush_cache",
            target="memcached-cache",
            parameters={
                "cache_type": "memcached",
                "host": "localhost",
                "port": 11211
            },
            priority=6,
            timeout=10.0
        )
        
        result = await handler.execute(action)
        
        assert result.success is True
        assert result.action_type == "flush_cache"
        assert result.target == "memcached-cache"
        
        mock_subprocess.assert_called_once_with(
            "echo 'flush_all' | nc localhost 11211",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
    
    async def test_execute_invalid_cache_type(self, config):
        """Test handling of invalid cache type."""
        handler = CacheRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_flush_3",
            action_type="flush_cache",
            target="unknown-cache",
            parameters={
                "cache_type": "invalid_type"
            },
            priority=6,
            timeout=10.0
        )
        
        result = await handler.execute(action)
        
        assert result.success is False
        assert "Unsupported cache type" in result.error_message


class TestServiceRecoveryHandler:
    """Test cases for service restart recovery actions."""
    
    @pytest.fixture
    def config(self):
        """Create a recovery config for testing."""
        return RecoveryConfig(
            enable_service_recovery=True,
            action_timeout=60.0
        )
    
    def test_service_handler_initialization(self, config):
        """Test service handler initialization."""
        handler = ServiceRecoveryHandler(config)
        
        assert handler.config == config
    
    def test_can_handle_service_actions(self, config):
        """Test that service handler can handle service actions."""
        handler = ServiceRecoveryHandler(config)
        
        assert handler.can_handle('restart_service') is True
        assert handler.can_handle('kill_process') is True
        assert handler.can_handle('flush_cache') is False
        assert handler.can_handle('restart_container') is False
    
    @patch('asyncio.create_subprocess_exec')
    @patch('src.healer.ServiceRecoveryHandler._wait_for_service_active')
    async def test_execute_restart_service_success(self, mock_wait_active, mock_subprocess, config):
        """Test successful service restart."""
        # Setup subprocess mocks
        mock_check_process = AsyncMock()
        mock_check_process.returncode = 0
        mock_check_process.communicate.return_value = (b"active", b"")
        
        mock_restart_process = AsyncMock()
        mock_restart_process.returncode = 0
        mock_restart_process.communicate.return_value = (b"", b"")
        
        mock_subprocess.side_effect = [mock_check_process, mock_restart_process]
        
        handler = ServiceRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_restart_service_1",
            action_type="restart_service",
            target="nginx",
            priority=8,
            timeout=30.0
        )
        
        result = await handler.execute(action)
        
        assert result.success is True
        assert result.action_type == "restart_service"
        assert result.target == "nginx"
        
        # Verify subprocess calls
        assert mock_subprocess.call_count == 2
        mock_wait_active.assert_called_once_with("nginx", 30.0)
    
    @patch('asyncio.create_subprocess_exec')
    async def test_execute_kill_process_success(self, mock_subprocess, config):
        """Test successful process kill."""
        mock_pgrep_process = AsyncMock()
        mock_pgrep_process.returncode = 0
        mock_pgrep_process.communicate.return_value = (b"1234", b"")
        
        mock_pkill_process = AsyncMock()
        mock_pkill_process.returncode = 0
        mock_pkill_process.communicate.return_value = (b"", b"")
        
        mock_subprocess.side_effect = [mock_pgrep_process, mock_pkill_process]
        
        handler = ServiceRecoveryHandler(config)
        
        action = RecoveryAction(
            action_id="test_kill_process_1",
            action_type="kill_process",
            target="python-app",
            parameters={"signal": "TERM"},
            priority=7,
            timeout=10.0
        )
        
        result = await handler.execute(action)
        
        assert result.success is True
        assert result.action_type == "kill_process"
        assert result.target == "python-app"
        
        # Verify subprocess calls
        assert mock_subprocess.call_count == 2
        mock_subprocess.assert_any_call(
            'pgrep', 'python-app',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        mock_subprocess.assert_any_call(
            'pkill', '-f', '-TERM', 'python-app',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )


class TestRecoveryEngine:
    """Test cases for the main recovery engine."""
    
    @pytest.fixture
    def config(self):
        """Create a recovery config for testing."""
        return RecoveryConfig(
            enable_docker_recovery=True,
            enable_cache_recovery=True,
            enable_service_recovery=True,
            max_concurrent_actions=3,
            action_timeout=120.0,
            retry_enabled=True
        )
    
    @patch('docker.from_env')
    def test_recovery_engine_initialization(self, mock_docker_client, config):
        """Test recovery engine initialization with all handlers."""
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client
        
        engine = RecoveryEngine(config)
        
        assert engine.config == config
        assert len(engine._handlers) == 3  # Docker, Cache, Service handlers
        assert engine._active_actions == {}
    
    @patch('docker.from_env')
    def test_recovery_engine_initialization_without_docker(self, mock_docker_client, config):
        """Test recovery engine initialization when Docker is unavailable."""
        mock_docker_client.side_effect = Exception("Docker not available")
        
        engine = RecoveryEngine(config)
        
        assert len(engine._handlers) == 2  # Only Cache and Service handlers
        assert engine._active_actions == {}
    
    @patch('docker.from_env')
    async def test_trigger_recovery_no_anomaly(self, mock_docker_client, config):
        """Test that no recovery actions are triggered when no anomaly is detected."""
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client
        
        engine = RecoveryEngine(config)
        
        # Create a non-anomalous result
        from src.detector import AnomalyResult
        anomaly = AnomalyResult(
            timestamp=datetime.now(),
            metric_type="test",
            anomaly_detected=False,
            confidence_score=0.1,
            metric_values={},
            severity_level="low"
        )
        
        results = await engine.trigger_recovery(anomaly)
        
        assert results == []
    
    @patch('docker.from_env')
    @patch.object(DockerRecoveryHandler, 'can_handle', return_value=True)
    @patch.object(DockerRecoveryHandler, 'execute')
    async def test_trigger_recovery_with_actions(self, mock_execute, mock_can_handle, mock_docker_client, config):
        """Test recovery engine triggering actions for detected anomalies."""
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client
        
        # Mock successful execution
        mock_execute.return_value = RecoveryResult(
            action_id="test_action_1",
            timestamp=datetime.now(),
            success=True,
            action_type="restart_container",
            target="test-container",
            execution_time=2.5,
            retry_count=0,
            final_state="healthy"
        )
        
        engine = RecoveryEngine(config)
        
        # Create an anomalous result
        from src.detector import AnomalyResult
        anomaly = AnomalyResult(
            timestamp=datetime.now(),
            metric_type="system_isolation_forest",
            anomaly_detected=True,
            confidence_score=0.9,
            metric_values={"cpu_percent": 95.0},
            severity_level="critical"
        )
        
        results = await engine.trigger_recovery(anomaly)
        
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action_type == "restart_container"
        assert results[0].target == "test-container"
    
    @patch('docker.from_env')
    @patch.object(DockerRecoveryHandler, 'can_handle', return_value=True)
    @patch.object(DockerRecoveryHandler, 'execute')
    async def test_trigger_recovery_with_exception(self, mock_execute, mock_can_handle, mock_docker_client, config):
        """Test recovery engine handling exceptions during action execution."""
        mock_client = MagicMock()
        mock_docker_client.return_value = mock_client
        
        # Mock execution that raises an exception
        mock_execute.side_effect = Exception("Execution failed")
        
        engine = RecoveryEngine(config)
        
        from src.detector import AnomalyResult
        anomaly = AnomalyResult(
            timestamp=datetime.now(),
            metric_type="system_isolation_forest",
            anomaly_detected=True,
            confidence_score=0.9,
            metric_values={"cpu_percent": 95.0},
            severity_level="critical"
        )
        
        results = await engine.trigger_recovery(anomaly)
        
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_message is not None and "Execution failed" in results[0].error_message
        assert results[0].final_state == "failed"


class TestRecoveryEngineIntegration:
    """Integration tests for the recovery engine."""
    
    @patch('docker.from_env')
    def test_recovery_engine_with_monitor_only_mode(self, mock_docker_client):
        """Test recovery engine behavior in monitor-only mode (Docker unavailable)."""
        # Simulate Docker being unavailable
        mock_docker_client.side_effect = Exception("Docker daemon not running")
        
        config = RecoveryConfig(
            enable_docker_recovery=True,  # Enabled but Docker unavailable
            enable_cache_recovery=True,
            enable_service_recovery=True,
            max_concurrent_actions=3
        )
        
        engine = RecoveryEngine(config)
        
        # Should initialize successfully with only cache and service handlers
        assert len(engine._handlers) == 2
        
        # Verify handlers are the correct types
        handler_types = [type(h).__name__ for h in engine._handlers]
        assert "CacheRecoveryHandler" in handler_types
        assert "ServiceRecoveryHandler" in handler_types
        assert "DockerRecoveryHandler" not in handler_types
        
        # Should still be able to trigger recovery for non-Docker actions
        from src.detector import AnomalyResult
        anomaly = AnomalyResult(
            timestamp=datetime.now(),
            metric_type="api_response_time",
            anomaly_detected=True,
            confidence_score=0.8,
            metric_values={"response_time_ms": 5000.0},
            severity_level="high"
        )
        
        # This should work without Docker
        # (Note: actual execution would require mocking the handlers)
        assert engine._generate_recovery_actions(anomaly)  # Should generate cache flush action