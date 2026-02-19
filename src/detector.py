"""
Anomaly detection service for Aegis Sentinel.

This module implements machine learning-based anomaly detection using
Isolation Forest algorithms and statistical methods to identify system
stress or abnormal behavior patterns in collected metrics.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from pydantic import BaseModel, Field, validator
from sklearn.ensemble import IsolationForest

from .monitor import APIMetrics, SystemMetrics

logger = logging.getLogger(__name__)


class AnomalyResult(BaseModel):
    """Data model for anomaly detection results."""
    
    timestamp: datetime = Field(description="Timestamp of anomaly detection")
    metric_type: str = Field(description="Type of metric being analyzed")
    anomaly_detected: bool = Field(description="Whether an anomaly was detected")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Confidence score of anomaly detection")
    metric_values: Dict[str, Union[float, int]] = Field(description="Current metric values")
    anomaly_description: Optional[str] = Field(default=None, description="Description of the anomaly")
    severity_level: str = Field(description="Severity level of the anomaly")


@dataclass
class DetectionConfig:
    """Configuration for anomaly detection."""
    
    isolation_contamination: float = 0.1
    isolation_n_estimators: int = 100
    statistical_threshold_multiplier: float = 3.0
    min_samples_for_detection: int = 50
    ml_warmup_samples: int = 50  # Minimum samples before attempting ML training
    ml_failure_threshold: int = 3  # Number of ML failures before logging CRITICAL
    sliding_window_size: int = 100


class AnomalyDetector(ABC):
    """Abstract base class for anomaly detection algorithms."""
    
    @abstractmethod
    async def detect_anomaly(self, metrics: List[SystemMetrics]) -> AnomalyResult:
        """Detect anomalies in the provided metrics."""
        pass
    
    @abstractmethod
    def is_trained(self) -> bool:
        """Check if the detector has been trained on sufficient data."""
        pass


class IsolationForestDetector(AnomalyDetector):
    """Isolation Forest-based anomaly detection."""
    
    def __init__(self, config: DetectionConfig) -> None:
        """Initialize the Isolation Forest detector."""
        self.config = config
        self._model = None
        self._trained = False
        self._feature_columns = ['cpu_percent', 'memory_percent', 'disk_usage_percent']
        
        logger.info("IsolationForestDetector initialized", extra={
            "contamination": config.isolation_contamination,
            "n_estimators": config.isolation_n_estimators
        })
    
    async def detect_anomaly(self, metrics: List[SystemMetrics]) -> AnomalyResult:
        """Detect anomalies using Isolation Forest algorithm."""
        if not self._trained:
            await self._train_model(metrics)
        
        # ML WARM-UP: Check if we're still in warm-up phase
        if len(metrics) < self.config.ml_warmup_samples:
            # During warm-up, use INFO level instead of CRITICAL
            logger.info("ML model in warm-up phase - using statistical fallback", extra={
                "model_trained": self._trained,
                "model_exists": self._model is not None,
                "warmup_samples_collected": len(metrics),
                "warmup_samples_required": self.config.ml_warmup_samples
            })
            
            # DELEGATE TO STATISTICAL FALLBACK: Ensure monitoring continues
            return AnomalyResult(
                timestamp=datetime.now(),
                metric_type="system_isolation_forest_fallback",
                anomaly_detected=False,
                confidence_score=0.0,
                metric_values={},
                anomaly_description="ML model in warm-up phase - delegated to statistical detection",
                severity_level="unknown"
            )
        
        # FAIL-FAST ML VALIDATION: Check if model is properly trained and available
        if not self._trained or self._model is None or len(metrics) < self.config.min_samples_for_detection:
            # ML WARM-UP: Only log CRITICAL after warm-up phase is complete
            if len(metrics) >= self.config.ml_warmup_samples:
                # CRITICAL: Emit alert and delegate to statistical fallback
                logger.critical("ML model unavailable - SYSTEM BLINDNESS RISK DETECTED", extra={
                    "model_trained": self._trained,
                    "model_exists": self._model is not None,
                    "sufficient_data": len(metrics) >= self.config.min_samples_for_detection,
                    "required_samples": self.config.min_samples_for_detection,
                    "available_samples": len(metrics)
                })
            else:
                # During warm-up, use INFO level instead of CRITICAL
                logger.info("ML model in warm-up phase - using statistical fallback", extra={
                    "model_trained": self._trained,
                    "model_exists": self._model is not None,
                    "warmup_samples_collected": len(metrics),
                    "warmup_samples_required": self.config.ml_warmup_samples
                })
            
            # DELEGATE TO STATISTICAL FALLBACK: Ensure monitoring continues
            return AnomalyResult(
                timestamp=datetime.now(),
                metric_type="system_isolation_forest_fallback",
                anomaly_detected=False,
                confidence_score=0.0,
                metric_values={},
                anomaly_description="ML model unavailable - delegated to statistical detection",
                severity_level="unknown"
            )
        
        try:
            # Extract features from latest metrics
            latest_metrics = metrics[-1]
            features = np.array([[
                latest_metrics.cpu_percent,
                latest_metrics.memory_percent,
                latest_metrics.disk_usage_percent
            ]])
            
            # Predict anomaly - ML Safety: Explicit model state validation
            anomaly_score = self._model.decision_function(features)[0]
            is_anomaly = self._model.predict(features)[0] == -1
            
            # Calculate confidence score (normalize anomaly score)
            confidence_score = abs(anomaly_score) / (abs(anomaly_score) + 1.0)
            
            # Generate description
            description = None
            severity = "low"
            if is_anomaly:
                description = self._generate_anomaly_description(latest_metrics, anomaly_score)
                severity = self._calculate_severity(confidence_score)
            
            result = AnomalyResult(
                timestamp=datetime.now(),
                metric_type="system_isolation_forest",
                anomaly_detected=is_anomaly,
                confidence_score=confidence_score,
                metric_values={
                    "cpu_percent": latest_metrics.cpu_percent,
                    "memory_percent": latest_metrics.memory_percent,
                    "disk_usage_percent": latest_metrics.disk_usage_percent
                },
                anomaly_description=description,
                severity_level=severity
            )
            
            logger.debug("Isolation Forest anomaly detection completed", extra={
                "anomaly_detected": is_anomaly,
                "confidence_score": round(confidence_score, 3),
                "severity": severity
            })
            
            return result
            
        except Exception as e:
            # CRITICAL: Log error and delegate to statistical fallback
            logger.critical("ML model failed - SYSTEM BLINDNESS RISK DETECTED", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            
            # DELEGATE TO STATISTICAL FALLBACK: Ensure monitoring continues
            return AnomalyResult(
                timestamp=datetime.now(),
                metric_type="system_isolation_forest_fallback",
                anomaly_detected=False,
                confidence_score=0.0,
                metric_values={},
                anomaly_description=f"ML model failed: {str(e)} - delegated to statistical detection",
                severity_level="unknown"
            )
    
    def is_trained(self) -> bool:
        """Check if the detector has been trained."""
        return self._trained
    
    async def _train_model(self, metrics: List[SystemMetrics]) -> None:
        """Train the Isolation Forest model on historical data."""
        if len(metrics) < self.config.min_samples_for_detection:
            logger.warning("Insufficient data for training Isolation Forest model", extra={
                "required_samples": self.config.min_samples_for_detection,
                "available_samples": len(metrics)
            })
            
            # ML INITIALIZATION GUARD: Create synthetic data for initial training
            # This prevents "System Blindness" by ensuring the model can always be trained
            logger.info("Creating synthetic training data for Isolation Forest model")
            
            try:
                # Generate synthetic training data with realistic ranges
                synthetic_data = []
                for _ in range(self.config.min_samples_for_detection):
                    synthetic_data.append([
                        np.random.uniform(10.0, 80.0),  # CPU: 10-80%
                        np.random.uniform(20.0, 90.0),  # Memory: 20-90%
                        np.random.uniform(30.0, 95.0)   # Disk: 30-95%
                    ])
                
                training_array = np.array(synthetic_data)
                
                # Initialize and train model with synthetic data
                self._model = IsolationForest(
                    contamination=self.config.isolation_contamination,
                    n_estimators=self.config.isolation_n_estimators,
                    random_state=42
                )
                
                self._model.fit(training_array)
                self._trained = True
                
                logger.info("Isolation Forest model trained with synthetic data", extra={
                    "training_samples": len(synthetic_data),
                    "contamination": self.config.isolation_contamination,
                    "synthetic_training": True
                })
                
            except Exception as e:
                logger.error("Failed to create synthetic training data", exc_info=True, extra={
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                })
                # Still mark as trained to prevent infinite retry loops
                self._trained = False
            return
        
        try:
            # Extract training features
            training_data = []
            for metric in metrics[-self.config.min_samples_for_detection:]:
                training_data.append([
                    metric.cpu_percent,
                    metric.memory_percent,
                    metric.disk_usage_percent
                ])
            
            training_array = np.array(training_data)
            
            # Initialize and train model
            self._model = IsolationForest(
                contamination=self.config.isolation_contamination,
                n_estimators=self.config.isolation_n_estimators,
                random_state=42
            )
            
            self._model.fit(training_array)
            self._trained = True
            
            logger.info("Isolation Forest model trained successfully", extra={
                "training_samples": len(training_data),
                "contamination": self.config.isolation_contamination
            })
            
        except Exception as e:
            logger.error("Failed to train Isolation Forest model", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise
    
    def _generate_anomaly_description(self, metrics: SystemMetrics, anomaly_score: float) -> str:
        """Generate human-readable anomaly description."""
        components = []
        
        if metrics.cpu_percent > 80:
            components.append(f"High CPU usage: {metrics.cpu_percent:.1f}%")
        
        if metrics.memory_percent > 85:
            components.append(f"High memory usage: {metrics.memory_percent:.1f}%")
        
        if metrics.disk_usage_percent > 90:
            components.append(f"High disk usage: {metrics.disk_usage_percent:.1f}%")
        
        if not components:
            components.append("Unusual system behavior pattern detected")
        
        return f"Isolation Forest anomaly detected: {'; '.join(components)}"
    
    def _calculate_severity(self, confidence_score: float) -> str:
        """Calculate severity level based on confidence score."""
        if confidence_score >= 0.8:
            return "critical"
        elif confidence_score >= 0.6:
            return "high"
        elif confidence_score >= 0.4:
            return "medium"
        else:
            return "low"


class StatisticalDetector(AnomalyDetector):
    """Statistical-based anomaly detection using standard deviation."""
    
    def __init__(self, config: DetectionConfig) -> None:
        """Initialize the statistical detector."""
        self.config = config
        self._statistics: Dict[str, Dict[str, float]] = {}
        self._trained = False
        
        logger.info("StatisticalDetector initialized", extra={
            "threshold_multiplier": config.statistical_threshold_multiplier
        })
    
    async def detect_anomaly(self, metrics: List[SystemMetrics]) -> AnomalyResult:
        """Detect anomalies using statistical methods."""
        if not self._trained:
            await self._calculate_statistics(metrics)
        
        if not self._trained or len(metrics) < self.config.min_samples_for_detection:
            return AnomalyResult(
                timestamp=datetime.now(),
                metric_type="system_statistical",
                anomaly_detected=False,
                confidence_score=0.0,
                metric_values={},
                anomaly_description="Insufficient data for detection",
                severity_level="unknown"
            )
        
        try:
            latest_metrics = metrics[-1]
            anomalies = []
            max_deviation = 0.0
            
            # Check each metric against its statistical bounds
            for metric_name in ['cpu_percent', 'memory_percent', 'disk_usage_percent']:
                current_value = getattr(latest_metrics, metric_name)
                stats = self._statistics[metric_name]
                
                # Calculate z-score with absolute value for proper anomaly detection
                mean = stats['mean']
                std = stats['std']
                
                if std > 0:
                    # Z-SCORE NORMALIZATION: Use absolute value for z-score comparison
                    z_score = abs((current_value - mean) / std)
                    max_deviation = max(max_deviation, z_score)
                    
                    # Ensure we detect both high and low anomalies
                    if z_score > self.config.statistical_threshold_multiplier:
                        anomalies.append(f"{metric_name}: {current_value:.1f} (z-score: {z_score:.2f})")
            
            is_anomaly = len(anomalies) > 0
            confidence_score = min(max_deviation / self.config.statistical_threshold_multiplier, 1.0)
            
            description = None
            severity = "low"
            if is_anomaly:
                description = f"Statistical anomaly: {'; '.join(anomalies)}"
                severity = self._calculate_severity(confidence_score)
            
            result = AnomalyResult(
                timestamp=datetime.now(),
                metric_type="system_statistical",
                anomaly_detected=is_anomaly,
                confidence_score=confidence_score,
                metric_values={
                    "cpu_percent": latest_metrics.cpu_percent,
                    "memory_percent": latest_metrics.memory_percent,
                    "disk_usage_percent": latest_metrics.disk_usage_percent
                },
                anomaly_description=description,
                severity_level=severity
            )
            
            logger.debug("Statistical anomaly detection completed", extra={
                "anomaly_detected": is_anomaly,
                "confidence_score": round(confidence_score, 3),
                "severity": severity
            })
            
            return result
            
        except Exception as e:
            logger.error("Error in statistical detection", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise
    
    def is_trained(self) -> bool:
        """Check if the detector has been trained."""
        return self._trained
    
    async def _calculate_statistics(self, metrics: List[SystemMetrics]) -> None:
        """Calculate statistical measures for anomaly detection."""
        if len(metrics) < self.config.min_samples_for_detection:
            logger.warning("Insufficient data for statistical analysis", extra={
                "required_samples": self.config.min_samples_for_detection,
                "available_samples": len(metrics)
            })
            return
        
        try:
            # Calculate statistics for each metric
            for metric_name in ['cpu_percent', 'memory_percent', 'disk_usage_percent']:
                values = [getattr(metric, metric_name) for metric in metrics[-self.config.min_samples_for_detection:]]
                
                self._statistics[metric_name] = {
                    'mean': float(np.mean(values).item()),
                    'std': float(np.std(values).item()),
                    'min': float(np.min(values).item()),
                    'max': float(np.max(values).item())
                }
            
            self._trained = True
            
            logger.info("Statistical analysis completed", extra={
                "metrics_analyzed": list(self._statistics.keys()),
                "sample_size": self.config.min_samples_for_detection
            })
            
        except Exception as e:
            logger.error("Failed to calculate statistics", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise
    
    def _calculate_severity(self, confidence_score: float) -> str:
        """Calculate severity level based on confidence score."""
        if confidence_score >= 0.9:
            return "critical"
        elif confidence_score >= 0.7:
            return "high"
        elif confidence_score >= 0.5:
            return "medium"
        else:
            return "low"


class AnomalyDetectorService:
    """Main anomaly detection service that combines multiple detection methods."""
    
    def __init__(self, config: Optional[DetectionConfig] = None) -> None:
        """Initialize the anomaly detection service."""
        self.config = config or DetectionConfig()
        self._isolation_detector = IsolationForestDetector(self.config)
        self._statistical_detector = StatisticalDetector(self.config)
        
        # DETECTION CIRCUIT BREAKER: Prevent system from going blind if ML models fail
        # Simple circuit breaker implementation to avoid circular import
        self._ml_failures = 0
        self._ml_failure_threshold = self.config.ml_failure_threshold
        self._ml_recovery_timeout = 300.0
        self._last_failure_time = None
        self._statistical_only_mode = False
        
        # HEALTH CHECK METRICS: Track fallback usage for ML failures
        self._ml_fallback_count = 0
        self._ml_success_count = 0
        self._ml_error_count = 0
        
        # ML WARM-UP: Track initialization state
        self._ml_warmup_complete = False
        self._ml_warmup_samples_collected = 0
        
        logger.info("AnomalyDetectorService initialized", extra={
            "detection_methods": ["isolation_forest", "statistical"],
            "min_samples": self.config.min_samples_for_detection,
            "ml_circuit_breaker_threshold": self._ml_failure_threshold,
            "ml_circuit_breaker_timeout": 300.0,
            "ml_warmup_samples": self.config.ml_warmup_samples
        })
    
    async def detect_anomalies(self, metrics: List[SystemMetrics]) -> List[AnomalyResult]:
        """Detect anomalies using multiple detection methods."""
        if not metrics:
            logger.warning("No metrics provided for anomaly detection")
            return []
        
        # ML WARM-UP: Track sample collection for warm-up phase
        if not self._ml_warmup_complete:
            self._ml_warmup_samples_collected += 1
            if self._ml_warmup_samples_collected >= self.config.ml_warmup_samples:
                self._ml_warmup_complete = True
                logger.info("ML warm-up phase completed", extra={
                    "warmup_samples_collected": self._ml_warmup_samples_collected,
                    "warmup_samples_required": self.config.ml_warmup_samples
                })
        
        results = []
        
        try:
            # Run Isolation Forest detection
            isolation_result = await self._isolation_detector.detect_anomaly(metrics)
            results.append(isolation_result)
            
            # Run statistical detection
            statistical_result = await self._statistical_detector.detect_anomaly(metrics)
            results.append(statistical_result)
            
            # Log overall detection summary
            total_anomalies = sum(1 for result in results if result.anomaly_detected)
            logger.info("Anomaly detection completed", extra={
                "total_anomalies": total_anomalies,
                "detection_methods": len(results),
                "latest_timestamp": metrics[-1].timestamp.isoformat() if metrics else None,
                "ml_warmup_complete": self._ml_warmup_complete,
                "ml_warmup_samples": self._ml_warmup_samples_collected
            })
            
            return results
            
        except Exception as e:
            logger.error("Error in anomaly detection service", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise
    
    async def detect_api_anomalies(self, api_metrics: List[APIMetrics]) -> List[AnomalyResult]:
        """Detect anomalies in API performance metrics."""
        if not api_metrics:
            return []
        
        results = []
        
        try:
            # Group metrics by endpoint
            endpoints = {}
            for metric in api_metrics:
                if metric.endpoint not in endpoints:
                    endpoints[metric.endpoint] = []
                endpoints[metric.endpoint].append(metric)
            
            # Analyze each endpoint separately
            for endpoint, metrics in endpoints.items():
                if len(metrics) < 10:  # Minimum samples for API analysis
                    continue
                
                # Calculate API-specific statistics
                response_times = [m.response_time_ms for m in metrics if m.success]
                success_rates = []
                
                # Calculate rolling success rate
                window_size = min(20, len(metrics))
                for i in range(len(metrics) - window_size + 1):
                    window = metrics[i:i + window_size]
                    success_rate = sum(1 for m in window if m.success) / len(window)
                    success_rates.append(success_rate)
                
                # Check for anomalies
                latest_metric = metrics[-1]
                latest_success_rate = success_rates[-1] if success_rates else 0.5
                
                # Response time anomaly
                if response_times:
                    mean_response_time = np.mean(response_times)
                    std_response_time = np.std(response_times)
                    
                    if std_response_time > 0:
                        z_score = abs((latest_metric.response_time_ms - mean_response_time) / std_response_time)
                        if z_score > self.config.statistical_threshold_multiplier:
                            result = AnomalyResult(
                                timestamp=datetime.now(),
                                metric_type="api_response_time",
                                anomaly_detected=True,
                                confidence_score=min(z_score / self.config.statistical_threshold_multiplier, 1.0),
                                metric_values={
                                    "endpoint": endpoint,
                                    "response_time_ms": latest_metric.response_time_ms,
                                    "mean_response_time": mean_response_time,
                                    "z_score": z_score
                                },
                                anomaly_description=f"High response time for {endpoint}: {latest_metric.response_time_ms:.1f}ms",
                                severity_level="medium" if z_score < 4.0 else "high"
                            )
                            results.append(result)
                
                # Success rate anomaly
                if success_rates:
                    mean_success_rate = np.mean(success_rates)
                    std_success_rate = np.std(success_rates)
                    
                    if std_success_rate > 0:
                        z_score = abs((latest_success_rate - mean_success_rate) / std_success_rate)
                        if z_score > self.config.statistical_threshold_multiplier and latest_success_rate < 0.8:
                            result = AnomalyResult(
                                timestamp=datetime.now(),
                                metric_type="api_success_rate",
                                anomaly_detected=True,
                                confidence_score=min(z_score / self.config.statistical_threshold_multiplier, 1.0),
                                metric_values={
                                    "endpoint": endpoint,
                                    "success_rate": latest_success_rate,
                                    "mean_success_rate": mean_success_rate,
                                    "z_score": z_score
                                },
                                anomaly_description=f"Low success rate for {endpoint}: {latest_success_rate:.1%}",
                                severity_level="high" if latest_success_rate < 0.5 else "medium"
                            )
                            results.append(result)
            
            logger.info("API anomaly detection completed", extra={
                "endpoints_analyzed": len(endpoints),
                "anomalies_detected": len(results)
            })
            
            return results
            
        except Exception as e:
            logger.error("Error in API anomaly detection", exc_info=True, extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise
    
    def is_ready(self) -> bool:
        """Check if the detection service is ready for analysis."""
        return self._isolation_detector.is_trained() and self._statistical_detector.is_trained()