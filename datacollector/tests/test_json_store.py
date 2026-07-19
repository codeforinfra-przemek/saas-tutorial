import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from datacollector.storage import json_store


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
