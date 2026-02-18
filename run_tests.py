"""
Simple test runner for Aegis Sentinel.

This module provides a basic test runner that can execute the test suite
without requiring external test frameworks to be installed.
"""

import asyncio
import inspect
import sys
import traceback
from datetime import datetime
from typing import List, Tuple

# Import test modules
from tests.test_detector import (
    TestAnomalyDetectorService,
    TestAnomalyResult,
    TestIsolationForestDetector,
    TestStatisticalDetector,
)
from tests.test_monitor import TestAPIMetrics, TestSystemMetrics, TestSystemMonitor


class SimpleTestRunner:
    """Simple test runner that doesn't require external dependencies."""
    
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []
    
    def run_test_method(self, test_instance, method_name: str) -> bool:
        """Run a single test method."""
        self.tests_run += 1
        try:
            method = getattr(test_instance, method_name)
            if inspect.iscoroutinefunction(method):
                asyncio.run(method())
            else:
                method()
            self.tests_passed += 1
            print(f"PASS {test_instance.__class__.__name__}.{method_name}")
            return True
        except Exception as e:
            self.tests_failed += 1
            self.failures.append((test_instance.__class__.__name__, method_name, str(e)))
            print(f"FAIL {test_instance.__class__.__name__}.{method_name}: {e}")
            return False
    
    def run_test_class(self, test_class) -> Tuple[int, int]:
        """Run all test methods in a test class."""
        instance = test_class()
        test_methods = [method for method in dir(instance) if method.startswith('test_')]
        
        passed = 0
        failed = 0
        
        for method_name in test_methods:
            if self.run_test_method(instance, method_name):
                passed += 1
            else:
                failed += 1
        
        return passed, failed
    
    def run_all_tests(self) -> None:
        """Run all tests in the test suite."""
        print("Running Aegis Sentinel test suite...")
        print("=" * 50)
        
        test_classes = [
            TestSystemMetrics,
            TestAPIMetrics,
            TestSystemMonitor,
            TestAnomalyResult,
            TestIsolationForestDetector,
            TestStatisticalDetector,
            TestAnomalyDetectorService,
        ]
        
        total_passed = 0
        total_failed = 0
        
        for test_class in test_classes:
            print(f"\nRunning {test_class.__name__}:")
            passed, failed = self.run_test_class(test_class)
            total_passed += passed
            total_failed += failed
        
        print("\n" + "=" * 50)
        print(f"Tests run: {self.tests_run}")
        print(f"Passed: {total_passed}")
        print(f"Failed: {total_failed}")
        
        if self.failures:
            print("\nFailures:")
            for class_name, method_name, error in self.failures:
                print(f"  {class_name}.{method_name}: {error}")
        
        if total_failed == 0:
            print("\nAll tests passed!")
            sys.exit(0)
        else:
            print(f"\n{total_failed} test(s) failed!")
            sys.exit(1)


def main():
    """Main entry point for the test runner."""
    runner = SimpleTestRunner()
    try:
        runner.run_all_tests()
    except KeyboardInterrupt:
        print("\nTest run interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error during test run: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()