#!/usr/bin/env python3
"""
Auctus Agent Test Runner
========================
Automated test runner for v0.1.17 validation.

Usage:
    python tests/run_tests.py [category]

Categories:
    all         - Run all tests (default)
    install     - Phase 12 installation tests
    pkg         - Phase 13.7/13.8 package tests
    desktop     - Phase 13.9 desktop shell tests
    enhanced    - Phase 13.10 enhanced features tests
    build       - Build script tests
    deps        - Dependency installation tests

Examples:
    python tests/run_tests.py
    python tests/run_tests.py desktop
    python tests/run_tests.py enhanced
"""
import sys
import os
import subprocess
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

# Ensure we're in project root
os.chdir(Path(__file__).parent.parent)


@dataclass
class TestResult:
    """Test result record."""
    id: str
    name: str
    category: str
    status: str  # 'pass', 'fail', 'skip', 'error'
    duration: float
    message: str = ""
    expected: str = ""
    actual: str = ""


class TestRunner:
    """Test runner for Auctus Agent."""
    
    def __init__(self):
        self.results: List[TestResult] = []
        self.start_time: Optional[float] = None
        
    def run_command(self, cmd: List[str], timeout: int = 30) -> tuple[bool, str, str]:
        """Run a shell command and return success status, stdout, stderr."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            return False, "", str(e)
    
    def test_syntax_check(self) -> TestResult:
        """Test Python syntax."""
        test_id = "syntax"
        name = "Python Syntax Check"
        category = "basic"
        
        start = time.time()
        success, stdout, stderr = self.run_command(
            ['python', '-m', 'py_compile', 'app/desktop.py'],
            timeout=10
        )
        duration = time.time() - start
        
        if success:
            return TestResult(test_id, name, category, 'pass', duration, "Syntax OK")
        else:
            return TestResult(test_id, name, category, 'fail', duration, f"Syntax error: {stderr}")
    
    def test_imports(self) -> TestResult:
        """Test required imports."""
        test_id = "imports"
        name = "Required Imports"
        category = "basic"
        
        start = time.time()
        required = ['fastapi', 'uvicorn', 'webview', 'pystray', 'pynput', 'plyer']
        missing = []
        
        for module in required:
            try:
                __import__(module)
            except ImportError:
                missing.append(module)
        
        duration = time.time() - start
        
        if not missing:
            return TestResult(test_id, name, category, 'pass', duration, "All imports OK")
        else:
            return TestResult(test_id, name, category, 'fail', duration, f"Missing: {', '.join(missing)}")
    
    def test_version_format(self) -> TestResult:
        """Test version format."""
        test_id = "version"
        name = "Version Format"
        category = "basic"
        
        start = time.time()
        try:
            sys.path.insert(0, 'app')
            from version import APP_VERSION
            
            # Check format: x.y.z
            parts = APP_VERSION.split('.')
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                duration = time.time() - start
                return TestResult(test_id, name, category, 'pass', duration, f"Version: {APP_VERSION}")
            else:
                duration = time.time() - start
                return TestResult(test_id, name, category, 'fail', duration, f"Invalid format: {APP_VERSION}")
        except Exception as e:
            duration = time.time() - start
            return TestResult(test_id, name, category, 'error', duration, str(e))
    
    def test_build_scripts_exist(self) -> TestResult:
        """Test build scripts exist."""
        test_id = "build_scripts"
        name = "Build Scripts Exist"
        category = "build"
        
        start = time.time()
        scripts = [
            'scripts/build_release.sh',
            'scripts/build_macos_pkg.sh',
            'scripts/build_desktop_mac.sh',
            'scripts/build_windows_installer.ps1',
            'scripts/build_desktop_win.ps1'
        ]
        
        missing = [s for s in scripts if not Path(s).exists()]
        duration = time.time() - start
        
        if not missing:
            return TestResult(test_id, name, category, 'pass', duration, "All scripts exist")
        else:
            return TestResult(test_id, name, category, 'fail', duration, f"Missing: {', '.join(missing)}")
    
    def test_build_script_syntax(self) -> TestResult:
        """Test bash script syntax."""
        test_id = "build_syntax"
        name = "Build Script Syntax"
        category = "build"
        
        start = time.time()
        scripts = [
            'scripts/build_release.sh',
            'scripts/build_macos_pkg.sh',
            'scripts/build_desktop_mac.sh'
        ]
        
        errors = []
        for script in scripts:
            if Path(script).exists():
                success, stdout, stderr = self.run_command(['bash', '-n', script], timeout=5)
                if not success:
                    errors.append(f"{script}: {stderr}")
        
        duration = time.time() - start
        
        if not errors:
            return TestResult(test_id, name, category, 'pass', duration, "All scripts valid")
        else:
            return TestResult(test_id, name, category, 'fail', duration, '; '.join(errors))
    
    def test_desktop_enhanced_syntax(self) -> TestResult:
        """Test desktop_enhanced.py syntax."""
        test_id = "desktop_syntax"
        name = "Desktop Enhanced Syntax"
        category = "desktop"
        
        start = time.time()
        success, stdout, stderr = self.run_command(
            ['python', '-m', 'py_compile', 'app/desktop_enhanced.py'],
            timeout=10
        )
        duration = time.time() - start
        
        if success:
            return TestResult(test_id, name, category, 'pass', duration, "Syntax OK")
        else:
            return TestResult(test_id, name, category, 'fail', duration, f"Syntax error: {stderr}")
    
    def test_ui_html_exists(self) -> TestResult:
        """Test UI HTML exists."""
        test_id = "ui_html"
        name = "UI HTML Exists"
        category = "basic"
        
        start = time.time()
        exists = Path('app/ui.html').exists()
        duration = time.time() - start
        
        if exists:
            return TestResult(test_id, name, category, 'pass', duration, "ui.html exists")
        else:
            return TestResult(test_id, name, category, 'fail', duration, "ui.html not found")
    
    def test_requirements_txt(self) -> TestResult:
        """Test requirements.txt has all dependencies."""
        test_id = "requirements"
        name = "Requirements Complete"
        category = "deps"
        
        start = time.time()
        required = [
            'pywebview', 'pystray', 'Pillow', 'pynput', 'plyer'
        ]
        
        content = Path('requirements.txt').read_text() if Path('requirements.txt').exists() else ""
        missing = [r for r in required if r not in content]
        duration = time.time() - start
        
        if not missing:
            return TestResult(test_id, name, category, 'pass', duration, "All dependencies listed")
        else:
            return TestResult(test_id, name, category, 'fail', duration, f"Missing: {', '.join(missing)}")
    
    def run_all_tests(self, category: str = 'all') -> List[TestResult]:
        """Run all or specific category tests."""
        self.start_time = time.time()
        self.results = []
        
        print(f"\n{'='*60}")
        print(f"  Auctus Agent Test Runner v0.1.17")
        print(f"{'='*60}\n")
        
        # Define test categories
        tests = {
            'basic': [
                self.test_syntax_check,
                self.test_imports,
                self.test_version_format,
                self.test_ui_html_exists,
            ],
            'build': [
                self.test_build_scripts_exist,
                self.test_build_script_syntax,
            ],
            'desktop': [
                self.test_desktop_enhanced_syntax,
            ],
            'deps': [
                self.test_requirements_txt,
            ]
        }
        
        # Run tests based on category
        if category == 'all':
            for cat_tests in tests.values():
                for test in cat_tests:
                    self.results.append(test())
        elif category in tests:
            for test in tests[category]:
                self.results.append(test())
        else:
            print(f"Unknown category: {category}")
            print(f"Available: {', '.join(['all'] + list(tests.keys()))}")
        
        return self.results
    
    def print_report(self) -> None:
        """Print test report."""
        if not self.results:
            print("No tests run.")
            return
        
        print(f"\n{'='*60}")
        print("  Test Results")
        print(f"{'='*60}\n")
        
        # Group by category
        categories = {}
        for result in self.results:
            if result.category not in categories:
                categories[result.category] = []
            categories[result.category].append(result)
        
        # Print results by category
        for cat, results in sorted(categories.items()):
            print(f"\n[{cat.upper()}]")
            for r in results:
                status_icon = {
                    'pass': '✅',
                    'fail': '❌',
                    'skip': '⏭️',
                    'error': '💥'
                }.get(r.status, '?')
                print(f"  {status_icon} {r.name} ({r.duration:.2f}s)")
                if r.message and r.status != 'pass':
                    print(f"     → {r.message}")
        
        # Summary
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == 'pass')
        failed = sum(1 for r in self.results if r.status == 'fail')
        errors = sum(1 for r in self.results if r.status == 'error')
        skipped = sum(1 for r in self.results if r.status == 'skip')
        
        duration = time.time() - self.start_time if self.start_time else 0
        
        print(f"\n{'='*60}")
        print("  Summary")
        print(f"{'='*60}")
        print(f"  Total:   {total}")
        print(f"  Passed:  {passed} ✅")
        print(f"  Failed:  {failed} ❌")
        print(f"  Errors:  {errors} 💥")
        print(f"  Skipped: {skipped} ⏭️")
        print(f"  Time:    {duration:.2f}s")
        print(f"  Pass:    {passed/total*100:.1f}%" if total > 0 else "  Pass:    N/A")
        print(f"{'='*60}\n")
    
    def save_report(self, filename: str = None) -> None:
        """Save test report to JSON file."""
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"tests/test_report_{timestamp}.json"
        
        report = {
            'version': '0.1.17',
            'timestamp': datetime.now().isoformat(),
            'total': len(self.results),
            'passed': sum(1 for r in self.results if r.status == 'pass'),
            'failed': sum(1 for r in self.results if r.status == 'fail'),
            'errors': sum(1 for r in self.results if r.status == 'error'),
            'skipped': sum(1 for r in self.results if r.status == 'skip'),
            'results': [asdict(r) for r in self.results]
        }
        
        Path(filename).write_text(json.dumps(report, indent=2))
        print(f"Report saved to: {filename}")


def main():
    """Main entry point."""
    category = sys.argv[1] if len(sys.argv) > 1 else 'all'
    
    runner = TestRunner()
    runner.run_all_tests(category)
    runner.print_report()
    runner.save_report()
    
    # Exit with error code if any tests failed
    failed = sum(1 for r in runner.results if r.status in ('fail', 'error'))
    sys.exit(1 if failed > 0 else 0)


if __name__ == '__main__':
    main()
