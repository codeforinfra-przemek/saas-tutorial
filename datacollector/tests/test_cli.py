import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from datacollector.cli import main


class CollectorCliTests(TestCase):
    def test_offline_plan_command_creates_artifact_without_api(self):
        with TemporaryDirectory() as temporary_directory:
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "plan",
                        "--brand",
                        "Żabka",
                        "--country",
                        "PL",
                        "--depth",
                        "catalog",
                        "--offline",
                        "--output-dir",
                        temporary_directory,
                    ]
                )

            summary = json.loads(stdout.getvalue())
            plan_paths = list(Path(temporary_directory).rglob("plan.json"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["generated_by"], "offline")
            self.assertEqual(len(plan_paths), 1)

    def test_questions_command_is_read_only(self):
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                ["questions", "--country", "US", "--depth", "due_diligence"]
            )

        payload = json.loads(stdout.getvalue())
        question_ids = {question["id"] for question in payload["questions"]}
        self.assertEqual(exit_code, 0)
        self.assertIn("us.state_overlay", question_ids)
        self.assertGreater(payload["question_count"], 20)
