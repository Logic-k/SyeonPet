"""Safety checks for the opt-in synthetic network benchmark."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("benchmark_free_ai", ROOT / "tools" / "benchmark_free_ai.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkSafetyTests(unittest.TestCase):
    def test_requires_explicit_live_and_never_overwrites_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "results.jsonl"
            args = ["benchmark", "--provider", "jarvisclaw", "--output", str(target)]
            with patch.object(sys, "argv", args), patch.object(benchmark, "optimize_ai") as request:
                with self.assertRaises(SystemExit):
                    benchmark.main()
                request.assert_not_called()
            self.assertFalse(target.exists())
            target.write_text("keep", encoding="utf-8")
            with patch.object(sys, "argv", args + ["--live"]), patch.object(benchmark, "optimize_ai") as request:
                with self.assertRaises(SystemExit):
                    benchmark.main()
                request.assert_not_called()
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_completed_response_metadata_and_literal_checks_are_recorded(self):
        data = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "11월 18일"}}]}).encode()

        def response(*_args, **_kwargs):
            result = benchmark.optimizer_ai._read_result(io.BytesIO(data))
            return {"result": result, "notes": []}

        output = io.StringIO()
        with patch.object(benchmark, "optimize_ai", side_effect=response):
            benchmark.run("jarvisclaw", (("가상", "가상 요청", ("11월 18일",)),), output)
        record = json.loads(output.getvalue())
        self.assertEqual(record["finish_reason"], "stop")
        self.assertEqual(record["literal_checks_missing_before_restoration"], [])
        self.assertFalse(record["app_reattached_source"])

    def test_role_scenarios_are_opt_in_and_include_both_directions_and_unknown_sender(self):
        self.assertEqual([item[0] for item in benchmark.ROLE_CASES],
                         ["학교가 문의", "교육지원청이 문의", "학부모가 문의", "발신자 미정", "문의자 직접 작성"])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "roles.jsonl"
            args = ["benchmark", "--live", "--provider", "jarvisclaw",
                    "--role-cases", "--output", str(target)]
            with patch.object(sys, "argv", args), patch.object(benchmark, "run") as run:
                self.assertEqual(benchmark.main(), 0)
            self.assertEqual(run.call_args.args[1], benchmark.ROLE_CASES)
            self.assertTrue(target.exists())

    def test_conflicting_role_and_existing_case_options_create_no_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "roles.jsonl"
            args = ["benchmark", "--live", "--provider", "jarvisclaw", "--role-cases",
                    "--case", "문의 회신", "--output", str(target)]
            with patch.object(sys, "argv", args), patch.object(benchmark, "run") as run:
                with self.assertRaises(SystemExit):
                    benchmark.main()
                run.assert_not_called()
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
