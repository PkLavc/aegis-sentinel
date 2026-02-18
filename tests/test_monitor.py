"""
Tests for the System Monitor module.

This module contains comprehensive unit tests for the SystemMonitor class
and related components, ensuring proper functionality of system metrics
collection and API monitoring.
"""

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# Importações locais
from src.monitor import APIMetrics, MonitoringConfig, SystemMetrics, SystemMonitor


class TestSystemMetrics:
    """Test cases for SystemMetrics data model."""
    
    def test_system_metrics_creation(self):
        """Test that SystemMetrics can be created with valid data."""
        timestamp = datetime.now()
        metrics = SystemMetrics(
            timestamp=timestamp,
            cpu_percent=50.0,
            memory_percent=60.0,
            memory_used_gb=4.0,
            memory_total_gb=8.0,
            disk_usage_percent=70.0,
            network_bytes_sent=1000,
            network_bytes_recv=2000
        )
        
        assert metrics.timestamp == timestamp
        assert metrics.cpu_percent == 50.0
        assert metrics.memory_percent == 60.0
        assert metrics.memory_used_gb == 4.0
        assert metrics.memory_total_gb == 8.0
        assert metrics.disk_usage_percent == 70.0
        assert metrics.network_bytes_sent == 1000
        assert metrics.network_bytes_recv == 2000
    
    def test_system_metrics_validation(self):
        """Test that SystemMetrics validates input data correctly."""
        # Test negative memory value
        try:
            SystemMetrics(
                timestamp=datetime.now(),
                cpu_percent=50.0,
                memory_percent=60.0,
                memory_used_gb=-1.0,  # Invalid
                memory_total_gb=8.0,
                disk_usage_percent=70.0,
                network_bytes_sent=1000,
                network_bytes_recv=2000
            )
            assert False, "Should have raised ValueError for negative memory"
        except ValueError as e:
            assert "Memory values must be positive" in str(e)
        
        # Test unreasonably high memory value
        try:
            SystemMetrics(
                timestamp=datetime.now(),
                cpu_percent=50.0,
                memory_percent=60.0,
                memory_used_gb=15000.0,  # Invalid
                memory_total_gb=8.0,
                disk_usage_percent=70.0,
                network_bytes_sent=1000,
                network_bytes_recv=2000
            )
            assert False, "Should have raised ValueError for high memory"
        except ValueError as e:
            assert "Memory values appear unreasonably high" in str(e)


class TestAPIMetrics:
    """Test cases for APIMetrics data model."""
    
    def test_api_metrics_creation(self):
        """Test that APIMetrics can be created with valid data."""
        timestamp = datetime.now()
        metrics = APIMetrics(
            timestamp=timestamp,
            endpoint="https://api.example.com/health",
            response_time_ms=150.0,
            status_code=200,
            success=True
        )
        
        assert metrics.timestamp == timestamp
        assert metrics.endpoint == "https://api.example.com/health"
        assert metrics.response_time_ms == 150.0
        assert metrics.status_code == 200
        assert metrics.success is True
    
    def test_api_metrics_with_error(self):
        """Test that APIMetrics handles error cases correctly."""
        timestamp = datetime.now()
        metrics = APIMetrics(
            timestamp=timestamp,
            endpoint="https://api.example.com/health",
            response_time_ms=500.0,
            status_code=500,
            success=False,
            error_message="Internal Server Error"
        )
        
        assert metrics.success is False
        assert metrics.error_message == "Internal Server Error"


class TestSystemMonitor:
    """Test cases for SystemMonitor class."""
    
    def create_monitor_config(self):
        """Create a test monitoring configuration."""
        return MonitoringConfig(
            collection_interval=1.0,
            api_endpoints=["https://httpbin.org/delay/1"],
            enable_network_monitoring=True,
            enable_disk_monitoring=True
        )
    
    def create_monitor(self):
        """Create a SystemMonitor instance for testing."""
        return SystemMonitor(self.create_monitor_config())
    
    @patch('src.monitor.psutil')
    @patch('src.monitor.aiohttp')
    async def test_collect_system_metrics(self, mock_aiohttp, mock_psutil):
        """Test that system metrics are collected correctly."""
        monitor = self.create_monitor()
        # Mock psutil calls
        mock_psutil.cpu_percent.return_value = 50.0
        
        mock_memory = MagicMock()
        mock_memory.percent = 60.0
        mock_memory.used = 4 * (1024**3)  # 4 GB
        mock_memory.total = 8 * (1024**3)  # 8 GB
        mock_psutil.virtual_memory.return_value = mock_memory
        
        mock_disk = MagicMock()
        mock_disk.used = 70 * (1024**3)  # 70 GB
        mock_disk.total = 100 * (1024**3)  # 100 GB
        mock_psutil.disk_usage.return_value = mock_disk
        
        mock_net = MagicMock()
        mock_net.bytes_sent = 1000
        mock_net.bytes_recv = 2000
        mock_psutil.net_io_counters.return_value = mock_net
        
        # Collect metrics
        metrics = await monitor._collect_system_metrics()
        
        # Verify results
        assert isinstance(metrics, SystemMetrics)
        assert metrics.cpu_percent == 50.0
        assert metrics.memory_percent == 60.0
        assert metrics.memory_used_gb == 4.0
        assert metrics.memory_total_gb == 8.0
        assert metrics.disk_usage_percent == 70.0
        assert metrics.network_bytes_sent == 1000
        assert metrics.network_bytes_recv == 2000
    
    @patch('src.monitor.aiohttp')
    async def test_collect_api_metrics_success(self, mock_aiohttp):
        """Test that API metrics are collected correctly for successful requests."""
        monitor = self.create_monitor()
        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        
        mock_session = AsyncMock()
        mock_session.get.return_value = mock_response
        mock_aiohttp.ClientSession.return_value.__aenter__.return_value = mock_session
        
        # Mock time for response time calculation
        with patch('time.time', side_effect=[1000.0, 1001.0]):  # 1 second response time
            metrics = await monitor._collect_api_metrics()
        
        assert len(metrics) == 1
        assert isinstance(metrics[0], APIMetrics)
        assert metrics[0].endpoint == "https://httpbin.org/delay/1"
        assert metrics[0].response_time_ms == 1000.0  # 1 second = 1000ms
        assert metrics[0].status_code == 200
        assert metrics[0].success is True
    
    @patch('src.monitor.aiohttp')
    async def test_collect_api_metrics_timeout(self, mock_aiohttp):
        """Test that API metrics handle timeout correctly."""
        monitor = self.create_monitor()
        # Mock asyncio.TimeoutError
        mock_session = AsyncMock()
        mock_session.get.side_effect = asyncio.TimeoutError()
        mock_aiohttp.ClientSession.return_value.__aenter__.return_value = mock_session
        
        metrics = await monitor._collect_api_metrics()
        
        assert len(metrics) == 1
        assert isinstance(metrics[0], APIMetrics)
        assert metrics[0].response_time_ms == 10000.0  # Timeout threshold
        assert metrics[0].status_code == 0
        assert metrics[0].success is False
        assert metrics[0].error_message == "Request timeout"
    
    async def test_monitoring_loop(self):
        """Test that the monitoring loop runs correctly."""
        monitor = self.create_monitor()
        # Mock the collection methods
        mock_system_metrics = SystemMetrics(
            timestamp=datetime.now(),
            cpu_percent=50.0,
            memory_percent=60.0,
            memory_used_gb=4.0,
            memory_total_gb=8.0,
            disk_usage_percent=70.0,
            network_bytes_sent=1000,
            network_bytes_recv=2000
        )
        
        monitor._collect_system_metrics = AsyncMock(return_value=mock_system_metrics)
        monitor._collect_api_metrics = AsyncMock(return_value=[])
        
        # Start monitoring
        await monitor.start_monitoring()
        
        # Let it run for a short time
        await asyncio.sleep(1.5)
        
        # Stop monitoring
        await monitor.stop_monitoring()
        
        # Verify that collection methods were called
        assert monitor._collect_system_metrics.call_count > 0
        assert monitor._collect_api_metrics.call_count > 0
        
        # Verify that metrics were collected
        latest_metrics = monitor.get_latest_metrics()
        assert len(latest_metrics) > 0
        assert isinstance(latest_metrics[0], SystemMetrics)
    
    def test_get_latest_metrics(self):
        """Test that latest metrics are retrieved correctly."""
        monitor = self.create_monitor()
        # Add some test metrics
        test_metrics = [
            SystemMetrics(
                timestamp=datetime.now(),
                cpu_percent=50.0,
                memory_percent=60.0,
                memory_used_gb=4.0,
                memory_total_gb=8.0,
                disk_usage_percent=70.0,
                network_bytes_sent=1000,
                network_bytes_recv=2000
            )
        ]
        monitor._metrics_buffer = test_metrics
        
        latest = monitor.get_latest_metrics()
        assert len(latest) == 1
        assert latest[0] == test_metrics[0]
    
    def test_get_latest_api_metrics(self):
        """Test that latest API metrics are retrieved correctly."""
        monitor = self.create_monitor()
        # Add some test API metrics
        test_metrics = [
            APIMetrics(
                timestamp=datetime.now(),
                endpoint="https://api.example.com/health",
                response_time_ms=150.0,
                status_code=200,
                success=True
            )
        ]
        monitor._api_metrics_buffer = test_metrics
        
        latest = monitor.get_latest_api_metrics()
        assert len(latest) == 1
        assert latest[0] == test_metrics[0]
    
    def test_get_metrics_summary(self):
        """Test that metrics summary is generated correctly."""
        monitor = self.create_monitor()
        # Add test metrics
        test_metrics = [
            SystemMetrics(
                timestamp=datetime.now(),
                cpu_percent=50.0,
                memory_percent=60.0,
                memory_used_gb=4.0,
                memory_total_gb=8.0,
                disk_usage_percent=70.0,
                network_bytes_sent=1000,
                network_bytes_recv=2000
            )
        ]
        monitor._metrics_buffer = test_metrics
        
        summary = monitor.get_metrics_summary()
        
        assert summary["cpu_percent"] == 50.0
        assert summary["memory_percent"] == 60.0
        assert summary["memory_used_gb"] == 4.0
        assert summary["disk_usage_percent"] == 70.0
        assert summary["network_bytes_sent"] == 1000
        assert summary["network_bytes_recv"] == 2000
        assert "timestamp" in summary
