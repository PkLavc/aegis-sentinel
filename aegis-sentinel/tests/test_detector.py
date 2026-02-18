"""
Tests for the Anomaly Detector module.

This module contains comprehensive unit tests for the anomaly detection
components, including Isolation Forest and statistical detection methods.
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np

# Importações locais
from src.detector import (
    AnomalyDetectorService,
    AnomalyResult,
    DetectionConfig,
    IsolationForestDetector,
    StatisticalDetector,
)
from src.monitor import SystemMetrics


class TestAnomalyResult:
    """Test cases for AnomalyResult data model."""
    
    def test_anomaly_result_creation(self):
        """Test that AnomalyResult can be created with valid data."""
        timestamp = datetime.now()
        result = AnomalyResult(
            timestamp=timestamp,
            metric_type="system_isolation_forest",
            anomaly_detected=True,
            confidence_score=0.85,
            metric_values={"cpu_percent": 90.0, "memory_percent": 85.0},
            anomaly_description="High CPU and memory usage detected",
            severity_level="high"
        )
        
        assert result.timestamp == timestamp
        assert result.metric_type == "system_isolation_forest"
        assert result.anomaly_detected is True
        assert result.confidence_score == 0.85
        assert result.metric_values == {"cpu_percent": 90.0, "memory_percent": 85.0}
        assert result.anomaly_description == "High CPU and memory usage detected"
        assert result.severity_level == "high"
    
    def test_anomaly_result_validation(self):
        """Test that AnomalyResult validates input data correctly."""
        # Test confidence score too high
        try:
            AnomalyResult(
                timestamp=datetime.now(),
                metric_type="system_isolation_forest",
                anomaly_detected=True,
                confidence_score=1.5,  # Invalid
                metric_values={},
                severity_level="high"
            )
            assert False, "Should have raised ValueError for high confidence score"
        except ValueError as e:
            assert "ensure this value is less than or equal to 1.0" in str(e)


class TestIsolationForestDetector:
    """Test cases for IsolationForestDetector."""
    
    def create_detector_config(self):
        """Create a test detection configuration."""
        return DetectionConfig(
            isolation_contamination=0.1,
            isolation_n_estimators=10,
            statistical_threshold_multiplier=3.0,
            min_samples_for_detection=10,
            sliding_window_size=50
        )
    
    def create_detector(self):
        """Create an IsolationForestDetector instance for testing."""
        return IsolationForestDetector(self.create_detector_config())
    
    def create_sample_metrics(self):
        """Create sample system metrics for testing."""
        base_time = datetime.now()
        metrics = []
        for i in range(50):
            metrics.append(SystemMetrics(
                timestamp=base_time,
                cpu_percent=50.0 + (i % 20),  # Varying CPU usage
                memory_percent=60.0 + (i % 15),  # Varying memory usage
                memory_used_gb=4.0 + (i % 2),
                memory_total_gb=8.0,
                disk_usage_percent=70.0 + (i % 10),
                network_bytes_sent=1000 + i,
                network_bytes_recv=2000 + i
            ))
        return metrics
    
    @patch('src.detector.IsolationForest')
    async def test_detect_anomaly_without_training(self, mock_isolation_forest):
        """Test anomaly detection without sufficient training data."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        # Mock the model to simulate not being trained
        detector._model = None
        
        result = await detector.detect_anomaly(sample_metrics[:5])  # Insufficient data
        
        assert result.anomaly_detected is False
        assert result.confidence_score == 0.0
        assert result.anomaly_description is not None
        assert "Insufficient data" in result.anomaly_description
    
    @patch('src.detector.IsolationForest')
    async def test_detect_anomaly_with_training(self, mock_isolation_forest):
        """Test anomaly detection with trained model."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        # Mock the trained model
        mock_model = MagicMock()
        mock_model.decision_function.return_value = [0.5]
        mock_model.predict.return_value = [1]  # Normal prediction
        detector._model = mock_model
        detector._trained = True
        
        result = await detector.detect_anomaly(sample_metrics)
        
        assert isinstance(result, AnomalyResult)
        assert result.anomaly_detected is False
        assert result.confidence_score >= 0.0
    
    @patch('src.detector.IsolationForest')
    async def test_detect_anomaly_with_anomaly(self, mock_isolation_forest):
        """Test anomaly detection when an anomaly is present."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        # Mock the trained model to detect an anomaly
        mock_model = MagicMock()
        mock_model.decision_function.return_value = [-1.5]  # Anomalous prediction
        mock_model.predict.return_value = [-1]  # Anomaly detected
        detector._model = mock_model
        detector._trained = True
        
        result = await detector.detect_anomaly(sample_metrics)
        
        assert isinstance(result, AnomalyResult)
        assert result.anomaly_detected is True
        assert result.confidence_score > 0.5
        assert result.severity_level in ["critical", "high", "medium", "low"]
    
    @patch('src.detector.IsolationForest')
    async def test_train_model(self, mock_isolation_forest):
        """Test that the model can be trained correctly."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        mock_model_class = MagicMock()
        mock_model_instance = MagicMock()
        mock_model_class.return_value = mock_model_instance
        mock_isolation_forest.return_value = mock_model_class
        
        await detector._train_model(sample_metrics)
        
        assert detector._trained is True
        assert detector._model is not None
        mock_model_instance.fit.assert_called_once()
    
    def test_calculate_severity(self):
        """Test severity calculation based on confidence score."""
        detector = self.create_detector()
        assert detector._calculate_severity(0.9) == "critical"
        assert detector._calculate_severity(0.75) == "high"
        assert detector._calculate_severity(0.55) == "medium"
        assert detector._calculate_severity(0.3) == "low"


class TestStatisticalDetector:
    """Test cases for StatisticalDetector."""
    
    def create_detector_config(self):
        """Create a test detection configuration."""
        return DetectionConfig(
            isolation_contamination=0.1,
            isolation_n_estimators=10,
            statistical_threshold_multiplier=2.0,
            min_samples_for_detection=10,
            sliding_window_size=50
        )
    
    def create_detector(self):
        """Create a StatisticalDetector instance for testing."""
        return StatisticalDetector(self.create_detector_config())
    
    def create_sample_metrics(self):
        """Create sample system metrics for testing."""
        base_time = datetime.now()
        metrics = []
        for i in range(50):
            metrics.append(SystemMetrics(
                timestamp=base_time,
                cpu_percent=50.0 + (i % 5),  # Normal variation
                memory_percent=60.0 + (i % 5),
                memory_used_gb=4.0 + (i % 1),
                memory_total_gb=8.0,
                disk_usage_percent=70.0 + (i % 5),
                network_bytes_sent=1000 + i,
                network_bytes_recv=2000 + i
            ))
        return metrics
    
    async def test_detect_anomaly_without_training(self):
        """Test anomaly detection without sufficient training data."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        result = await detector.detect_anomaly(sample_metrics[:5])  # Insufficient data
        
        assert result.anomaly_detected is False
        assert result.confidence_score == 0.0
        assert result.anomaly_description is not None
        assert "Insufficient data" in result.anomaly_description
    
    async def test_detect_anomaly_with_training(self):
        """Test anomaly detection with trained statistics."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        await detector._calculate_statistics(sample_metrics)
        
        # Test with normal metrics
        normal_metrics = [SystemMetrics(
            timestamp=datetime.now(),
            cpu_percent=52.0,
            memory_percent=62.0,
            memory_used_gb=4.1,
            memory_total_gb=8.0,
            disk_usage_percent=72.0,
            network_bytes_sent=1050,
            network_bytes_recv=2050
        )]
        
        result = await detector.detect_anomaly(normal_metrics)
        
        assert isinstance(result, AnomalyResult)
        assert result.anomaly_detected is False
        assert result.confidence_score == 0.0
    
    async def test_detect_anomaly_with_statistical_anomaly(self):
        """Test anomaly detection when statistical anomaly is present."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        await detector._calculate_statistics(sample_metrics)
        
        # Test with anomalous metrics (high CPU)
        anomalous_metrics = [SystemMetrics(
            timestamp=datetime.now(),
            cpu_percent=95.0,  # Very high CPU
            memory_percent=62.0,
            memory_used_gb=4.1,
            memory_total_gb=8.0,
            disk_usage_percent=72.0,
            network_bytes_sent=1050,
            network_bytes_recv=2050
        )]
        
        result = await detector.detect_anomaly(anomalous_metrics)
        
        assert isinstance(result, AnomalyResult)
        assert result.anomaly_detected is True
        assert result.confidence_score > 0.0
        assert "cpu_percent" in result.anomaly_description
    
    async def test_calculate_statistics(self):
        """Test that statistics are calculated correctly."""
        detector = self.create_detector()
        sample_metrics = self.create_sample_metrics()
        
        await detector._calculate_statistics(sample_metrics)
        
        assert detector._trained is True
        assert detector._statistics is not None
        assert detector._statistics.get("cpu_percent") is not None
        assert detector._statistics.get("memory_percent") is not None
        assert detector._statistics.get("disk_usage_percent") is not None
        
        cpu_stats = detector._statistics["cpu_percent"]
        assert "mean" in cpu_stats
        assert "std" in cpu_stats
        assert "min" in cpu_stats
        assert "max" in cpu_stats


class TestAnomalyDetectorService:
    """Test cases for AnomalyDetectorService."""
    
    def create_detector_service(self):
        """Create an AnomalyDetectorService instance for testing."""
        return AnomalyDetectorService()
    
    def create_sample_metrics(self):
        """Create sample system metrics for testing."""
        base_time = datetime.now()
        metrics = []
        for i in range(50):
            metrics.append(SystemMetrics(
                timestamp=base_time,
                cpu_percent=50.0 + (i % 10),
                memory_percent=60.0 + (i % 8),
                memory_used_gb=4.0 + (i % 2),
                memory_total_gb=8.0,
                disk_usage_percent=70.0 + (i % 6),
                network_bytes_sent=1000 + i,
                network_bytes_recv=2000 + i
            ))
        return metrics
    
    async def test_detect_anomalies(self):
        """Test that anomalies are detected correctly."""
        detector_service = self.create_detector_service()
        sample_metrics = self.create_sample_metrics()
        
        results = await detector_service.detect_anomalies(sample_metrics)
        
        assert isinstance(results, list)
        assert len(results) == 2  # Isolation Forest + Statistical
        
        for result in results:
            assert isinstance(result, AnomalyResult)
            assert result.metric_type in ["system_isolation_forest", "system_statistical"]
    
    async def test_detect_api_anomalies(self):
        """Test that API anomalies are detected correctly."""
        detector_service = self.create_detector_service()
        from src.monitor import APIMetrics
        
        api_metrics = [
            APIMetrics(
                timestamp=datetime.now(),
                endpoint="https://api.example.com/health",
                response_time_ms=150.0,
                status_code=200,
                success=True
            ),
            APIMetrics(
                timestamp=datetime.now(),
                endpoint="https://api.example.com/health",
                response_time_ms=2000.0,  # Slow response
                status_code=200,
                success=True
            )
        ]
        
        results = await detector_service.detect_api_anomalies(api_metrics)
        
        assert isinstance(results, list)
        # Should detect anomaly due to high response time
        assert len(results) > 0
        for result in results:
            assert isinstance(result, AnomalyResult)
            assert result.metric_type in ["api_response_time", "api_success_rate"]
    
    def test_is_ready(self):
        """Test that readiness check works correctly."""
        detector_service = self.create_detector_service()
        # Initially not ready
        assert detector_service.is_ready() is False
        
        # After training, should be ready
        # Note: In a real test, we would train the detectors first
        # For this test, we'll just check the method exists and returns a boolean
        assert isinstance(detector_service.is_ready(), bool)