import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from datacollector.storage import json_store


class FixtureCheckerResults:
    def __init__(self, *, iteration: int, generated_by: str) -> None:
        self.iteration = iteration
        self.generated_by = generated_by

    def model_dump(self, *, mode: str):
        if mode != "json":
            raise AssertionError("Checker artifacts must use JSON-mode serialization.")
        return {
            "iteration": self.iteration,
            "generated_by": self.generated_by,
            "brand_name": "Żabka",
        }


class FixtureResolverResults:
    def __init__(self, *, iteration: int, generated_by: str) -> None:
        self.iteration = iteration
        self.generated_by = generated_by

    def model_dump(self, *, mode: str):
        if mode != "json":
            raise AssertionError("Resolver artifacts must use JSON-mode serialization.")
        return {
            "iteration": self.iteration,
            "generated_by": self.generated_by,
            "brand_name": "Żabka",
        }


class FixtureExecutorResults:
    def __init__(self, *, iteration: int, execution_mode: str) -> None:
        self.iteration = iteration
        self.execution_mode = SimpleNamespace(value=execution_mode)

    def model_dump(self, *, mode: str):
        if mode != "json":
            raise AssertionError("Executor artifacts must use JSON-mode serialization.")
        return {
            "iteration": self.iteration,
            "execution_mode": self.execution_mode.value,
            "brand_name": "Żabka",
        }


class ImmutableWriteTests(TestCase):
    def test_publishes_complete_file_from_same_directory_and_fsyncs(self):
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "artifact.json"
            rendered = '{"complete": true}\n'
            real_link = os.link
            real_fsync = os.fsync
            published_from = None

            def checked_link(source, destination):
                nonlocal published_from
                published_from = Path(source)
                self.assertEqual(published_from.parent, target.parent)
                self.assertEqual(Path(destination), target)
                self.assertEqual(published_from.read_text(encoding="utf-8"), rendered)
                real_link(source, destination)

            with (
                patch.object(json_store.os, "link", side_effect=checked_link),
                patch.object(json_store.os, "fsync", wraps=real_fsync) as fsync,
            ):
                json_store._write_immutable_text(target, rendered)

            self.assertEqual(target.read_text(encoding="utf-8"), rendered)
            self.assertIsNotNone(published_from)
            self.assertFalse(published_from.exists())
            self.assertEqual(fsync.call_count, 2)

    def test_existing_target_is_never_replaced(self):
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "artifact.json"
            target.write_text("original\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                json_store._write_immutable_text(target, "replacement\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(list(target.parent.glob(".artifact.json.*.tmp")), [])

    def test_failed_file_fsync_leaves_no_target_or_temporary_file(self):
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "artifact.json"

            with (
                patch.object(
                    json_store.os,
                    "fsync",
                    side_effect=OSError("simulated storage failure"),
                ),
                self.assertRaises(OSError),
            ):
                json_store._write_immutable_text(target, '{"partial": false}\n')

            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.glob(".artifact.json.*.tmp")), [])


class CheckerStorageTests(TestCase):
    def test_checker_filename_variants_are_singular_and_iteration_aware(self):
        self.assertEqual(
            json_store.checker_results_filename_for(1, free=False),
            "check.json",
        )
        self.assertEqual(
            json_store.checker_results_filename_for(1, free=True),
            "check-free.json",
        )
        self.assertEqual(
            json_store.checker_results_filename_for(4, free=False),
            "check-r004.json",
        )
        self.assertEqual(
            json_store.checker_results_filename_for(4, free=True),
            "check-r004-free.json",
        )
        self.assertEqual(
            json_store.checker_results_filename(
                SimpleNamespace(iteration=7, generated_by="deterministic")
            ),
            "check-r007-free.json",
        )
        self.assertEqual(
            json_store.checker_results_filename(
                SimpleNamespace(iteration=7, generated_by="openai")
            ),
            "check-r007.json",
        )

    def test_checker_save_is_immutable_and_load_returns_exact_byte_hash(self):
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            extraction_path = directory / "extractions-r004.json"
            results = FixtureCheckerResults(
                iteration=4,
                generated_by="deterministic",
            )

            checker_path = json_store.save_checker_results(
                results,
                extraction_path,
            )
            raw_checker = checker_path.read_bytes()

            self.assertEqual(checker_path, directory / "check-r004-free.json")
            self.assertTrue(raw_checker.endswith(b"\n"))
            self.assertEqual(
                json.loads(raw_checker)["brand_name"],
                "Żabka",
            )

            with self.assertRaises(FileExistsError):
                json_store.save_checker_results(results, extraction_path)
            self.assertEqual(checker_path.read_bytes(), raw_checker)

            validated = object()
            with patch.object(
                json_store.CheckerResults,
                "model_validate_json",
                return_value=validated,
            ) as validate:
                loaded, digest = json_store.load_checker_results(checker_path)

            self.assertIs(loaded, validated)
            self.assertEqual(digest, hashlib.sha256(raw_checker).hexdigest())
            validate.assert_called_once_with(raw_checker)

    def test_checker_save_honors_explicit_output_directory(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_directory = root / "checks"
            results = FixtureCheckerResults(
                iteration=1,
                generated_by="openai",
            )

            checker_path = json_store.save_checker_results(
                results,
                root / "inputs" / "extractions.json",
                output_dir=output_directory,
            )

            self.assertEqual(checker_path, output_directory / "check.json")


class ResolverStorageTests(TestCase):
    def test_resolver_filename_variants_are_iteration_aware(self):
        self.assertEqual(
            json_store.resolver_results_filename_for(1, free=False),
            "resolution.json",
        )
        self.assertEqual(
            json_store.resolver_results_filename_for(5, free=True),
            "resolution-r005-free.json",
        )
        self.assertEqual(
            json_store.resolver_results_filename(
                SimpleNamespace(iteration=5, generated_by="openai")
            ),
            "resolution-r005.json",
        )

    def test_resolver_save_is_immutable_and_load_returns_exact_hash(self):
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            checker_path = directory / "check-r005.json"
            results = FixtureResolverResults(
                iteration=5,
                generated_by="deterministic",
            )

            resolution_path = json_store.save_resolver_results(
                results,
                checker_path,
            )
            raw_resolution = resolution_path.read_bytes()

            self.assertEqual(
                resolution_path,
                directory / "resolution-r005-free.json",
            )
            with self.assertRaises(FileExistsError):
                json_store.save_resolver_results(results, checker_path)

            validated = object()
            with patch.object(
                json_store.ResolverResults,
                "model_validate_json",
                return_value=validated,
            ) as validate:
                loaded, digest = json_store.load_resolver_results(
                    resolution_path
                )

            self.assertIs(loaded, validated)
            self.assertEqual(
                digest,
                hashlib.sha256(raw_resolution).hexdigest(),
            )
            validate.assert_called_once_with(raw_resolution)


class ExecutorStorageTests(TestCase):
    def test_executor_and_merged_artifact_names_preserve_free_marker(self):
        free_executor = FixtureExecutorResults(
            iteration=6,
            execution_mode="free",
        )
        paid_executor = FixtureExecutorResults(
            iteration=6,
            execution_mode="paid",
        )
        self.assertEqual(
            json_store.executor_results_filename_for(6, free=True),
            "execution-r006-free.json",
        )
        self.assertEqual(
            json_store.executor_results_filename(free_executor),
            "execution-r006-free.json",
        )
        self.assertEqual(
            json_store.executor_results_filename(paid_executor),
            "execution-r006.json",
        )
        self.assertEqual(
            json_store.search_results_filename(
                SimpleNamespace(
                    iteration=6,
                    generated_by="executor",
                    execution_mode="free",
                )
            ),
            "sources-r006-free.json",
        )
        self.assertEqual(
            json_store.extraction_results_filename(
                SimpleNamespace(
                    iteration=6,
                    generated_by="executor",
                    execution_mode="free",
                )
            ),
            "extractions-r006-free.json",
        )

    def test_executor_save_is_immutable_and_load_returns_exact_hash(self):
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            resolution_path = directory / "resolution-r005.json"
            results = FixtureExecutorResults(
                iteration=6,
                execution_mode="free",
            )

            execution_path = json_store.save_executor_results(
                results,
                resolution_path,
            )
            raw_execution = execution_path.read_bytes()

            self.assertEqual(
                execution_path,
                directory / "execution-r006-free.json",
            )
            with self.assertRaises(FileExistsError):
                json_store.save_executor_results(results, resolution_path)

            validated = object()
            with patch.object(
                json_store.ExecutorResults,
                "model_validate_json",
                return_value=validated,
            ) as validate:
                loaded, digest = json_store.load_executor_results(execution_path)

            self.assertIs(loaded, validated)
            self.assertEqual(digest, hashlib.sha256(raw_execution).hexdigest())
            validate.assert_called_once_with(raw_execution)
