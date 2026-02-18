#!/usr/bin/env python3
"""
Simple test runner for Aegis Sentinel project.

This script provides a basic test runner that can execute all test classes
in the tests directory without requiring external dependencies like pytest.
"""

import sys
import os
import importlib
import inspect
import asyncio
import traceback
from typing import List, Tuple

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def discover_test_classes() -> List[type]:
    """Discover all test classes in the tests directory."""
    test_classes = []
    
    # Import test modules
    test_modules = [
        'tests.test_monitor',
        'tests.test_detector'
    ]
    
    for module_name in test_modules:
        try:
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and 
                    name.startswith('Test') and 
                    obj.__module__ == module_name):
                    test_classes.append(obj)
        except ImportError as e:
            print(f"Warning: Could not import {module_name}: {e}")
    
    return test_classes

class SimpleTestRunner:
    """Simple test runner that mimics pytest functionality."""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def run_test_method(self, test_instance, method_name: str) -> bool:
        """Run a single test method."""
        try:
            method = getattr(test_instance, method_name)
            
            # Check if method is async
            if asyncio.iscoroutinefunction(method):
                # Run async test
                asyncio.run(method())
            else:
                # Run sync test
                method()
            
            print(f"PASS {test_instance.__class__.__name__}.{method_name}")
            return True
            
        except Exception as e:
            print(f"FAIL {test_instance.__class__.__name__}.{method_name}: {e}")
            self.errors.append(f"{test_instance.__class__.__name__}.{method_name}: {e}")
            return False
    
    def run_test_class(self, test_class) -> Tuple[int, int]:
        """Run all test methods in a test class."""
        print(f"\nRunning {test_class.__name__}:")
        
        # Create test instance
        instance = test_class()
        
        # Find all test methods
        test_methods = [name for name in dir(instance) 
                       if name.startswith('test_') and callable(getattr(instance, name))]
        
        if not test_methods:
            print("  No test methods found")
            return 0, 0
        
        passed = 0
        failed = 0
        
        for method_name in test_methods:
            if self.run_test_method(instance, method_name):
                passed += 1
            else:
                failed += 1
        
        return passed, failed
    
    def run_all_tests(self):
        """Run all discovered test classes."""
        print("Running Aegis Sentinel test suite...")
        print("=" * 50)
        
        test_classes = discover_test_classes()
        
        if not test_classes:
            print("No test classes found!")
            return
        
        total_passed = 0
        total_failed = 0
        
        for test_class in test_classes:
            passed, failed = self.run_test_class(test_class)
            total_passed += passed
            total_failed += failed
        
        # Print summary
        print("\n" + "=" * 50)
        print(f"Test Summary:")
        print(f"  Passed: {total_passed}")
        print(f"  Failed: {total_failed}")
        print(f"  Total:  {total_passed + total_failed}")
        
        if total_failed > 0:
            print(f"\nFailures:")
            for error in self.errors:
                print(f"  - {error}")
        
        return total_failed == 0

def main():
    """Main entry point for the test runner."""
    runner = SimpleTestRunner()
    success = runner.run_all_tests()
    
    if success:
        print("\nAll tests passed!")
        sys.exit(0)
    else:
        print(f"\nSome tests failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()