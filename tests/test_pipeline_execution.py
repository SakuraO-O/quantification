"""Execute the workflow shell with a fake Python command; no network or DB."""

import os
from pathlib import Path
import subprocess
import unittest


class PipelineExecutionTest(unittest.TestCase):
    def run_pipeline(self, schedule, fail_command):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/trend_observer_v2.yml").read_text()
        block = workflow.split("          failed=0\n", 1)[1]
        shell = "failed=0\n" + "\n".join(line[10:] if line.startswith("          ") else line for line in block.splitlines())
        shell = shell.replace('${{ github.event_name }}', 'schedule')
        fake = '''python() {
          shift 4
          echo "CALL:$*"
          if [ "$*" = "$FAIL_COMMAND" ]; then return 1; fi
          return 0
        }
        '''
        return subprocess.run(["bash", "-e", "-c", fake + shell],
                              env={**os.environ, "SCHEDULE": schedule, "FAIL_COMMAND": fail_command},
                              capture_output=True, text=True, timeout=5)

    def test_us_failure_does_not_cancel_fundamentals_or_publish(self):
        result = self.run_pipeline("30 23 * * *", "sync-market --market US")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CALL:sync-fundamentals", result.stdout)
        self.assertIn("CALL:publish-dashboard", result.stdout)
        self.assertIn("CALL:sync-market --market CN --trigger retry", result.stdout)

    def test_failed_publish_does_not_dispatch_old_report(self):
        result = self.run_pipeline("0,10,20,30 1 * * 1-6", "publish-dashboard")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("CALL:dispatch-feishu", result.stdout)

    def test_evening_retry_failure_does_not_cancel_valuation(self):
        result = self.run_pipeline("30 12 * * *", "sync-market --market CN --trigger retry")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CALL:sync-valuation --market CN --force", result.stdout)

    def test_successful_dispatch(self):
        result = self.run_pipeline("0,10,20,30 1 * * 1-6", "none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CALL:dispatch-feishu", result.stdout)
