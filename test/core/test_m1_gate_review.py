"""Independent M1 review regressions for externally supplied data and framing.

These cases came from reviewing the first implementation, rather than from
its internal structure. They run without provider access or a core install.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin" / "core" / "src"))

from devsquad.adapters import classify_cli
from devsquad.codex_protocol import JsonLinePeer
from devsquad.contracts import (
    ContractError, ExecutionIdentity, LaunchSpec, validate_launch_payload,
)
from devsquad.validation import validate_task


def launch() -> LaunchSpec:
    return LaunchSpec(
        1, "codex", "cli_exec", ("fake-codex",), "/tmp", None, 2,
        ExecutionIdentity("codex", None, None, None, None, None),
    )


class UntrustedInputReview(unittest.TestCase):
    def test_launch_rejects_invalid_types_without_coercion(self):
        cases = [
            ("schema boolean", {"schema_version": True}),
            ("timeout boolean", {"timeout_seconds": True}),
            ("timeout NaN", {"timeout_seconds": float("nan")}),
            ("timeout infinity", {"timeout_seconds": float("inf")}),
            ("timeout fraction", {"timeout_seconds": 1.5}),
            ("string argv", {"argv": "fake-codex"}),
            ("object argv", {"argv": {"fake-codex": True}}),
            ("empty argv", {"argv": []}),
            ("relative cwd", {"cwd": "relative"}),
            ("arbitrary environment", {"environment": {"UNDECLARED_FLAG": "1"}}),
        ]
        for name, change in cases:
            with self.subTest(name=name):
                value = launch().to_dict()
                value.update(change)
                with self.assertRaises(ContractError):
                    validate_launch_payload(value)

    def test_task_rejects_invalid_nested_values(self):
        original = json.loads(
            (ROOT / "docs/plans/engineering-team/examples/branch-review.json").read_text()
        )
        cases = [
            ("base ref", ("project", "base_ref"), {}),
            ("criterion id", ("acceptance", 0, "id"), {}),
            ("criterion description", ("acceptance", 0, "description"), []),
            ("check id", ("checks", 0, "id"), 123),
            ("check argv", ("checks", 0, "argv"), "echo hello"),
            ("check boolean", ("checks", 0, "required_to_pass"), "false"),
            ("session reference", ("origin", "session_ref"), {}),
            ("scope list", ("scope", "read_paths"), "src"),
            ("finite budget", ("budget", "wall_seconds"), float("nan")),
            ("fallback", ("routing", "overrides"), {
                "reviewer": {"profile_id": "fixture", "fallback": "anything"},
            }),
        ]
        for name, path, value in cases:
            with self.subTest(name=name):
                task = copy.deepcopy(original)
                target = task
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ContractError):
                    validate_task(task)

    def test_malformed_provider_frames_return_a_verdict(self):
        for frame in (42, None, ["bad"], {"type": "item.completed", "item": "bad"},
                      {"type": "item.completed", "item": 7}):
            with self.subTest(frame=frame):
                result = classify_cli(launch(), returncode=0, stdout=json.dumps(frame), stderr="")
                self.assertEqual(result.error_code, "CLI_ERROR")
                self.assertNotEqual(result.execution_status, "succeeded")

    def test_partial_native_message_is_not_completion(self):
        frame = {"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}
        result = classify_cli(launch(), returncode=0, stdout=json.dumps(frame), stderr="")
        self.assertEqual(result.error_code, "CLI_ERROR")
        self.assertNotEqual(result.execution_status, "succeeded")


class NativeFramingReview(unittest.TestCase):
    def test_two_frames_in_one_write_are_both_available(self):
        read_fd, write_fd = os.pipe()
        with os.fdopen(read_fd, "r") as reader, os.fdopen(write_fd, "w") as writer:
            peer = JsonLinePeer(reader, writer)
            writer.write('{"n":1}\n{"n":2}\n')
            writer.flush()
            self.assertEqual(peer.receive(0.2), {"n": 1})
            self.assertEqual(peer.receive(0.2), {"n": 2})

    def test_incomplete_frame_respects_receive_deadline(self):
        read_fd, write_fd = os.pipe()
        with os.fdopen(read_fd, "r") as reader, os.fdopen(write_fd, "w") as writer:
            peer = JsonLinePeer(reader, writer)
            writer.write("{")
            writer.flush()
            outcomes = []

            def receive():
                try:
                    outcomes.append(peer.receive(0.05))
                except Exception as exc:
                    outcomes.append(exc)

            worker = threading.Thread(target=receive, daemon=True)
            started = time.monotonic()
            worker.start()
            worker.join(0.4)
            exceeded_deadline = worker.is_alive()
            # Release a buggy blocking readline before asserting, so a failed
            # regression does not leave a test thread or pipe behind.
            if exceeded_deadline:
                writer.write('"late":true}\n')
                writer.flush()
                worker.join(1)
            self.assertFalse(exceeded_deadline, "receive ignored its deadline")
            self.assertLess(time.monotonic() - started, 0.4)
            self.assertIsInstance(outcomes[0], TimeoutError)


if __name__ == "__main__":
    unittest.main()
