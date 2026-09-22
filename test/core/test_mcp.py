import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "plugin/core"
sys.path.insert(0, str(CORE / "src"))

from devsquad import cli, mcp_server


class MCPDependencyBoundaryTest(unittest.TestCase):
    def test_supported_sdk_is_an_exact_optional_dependency(self):
        project = tomllib.loads((CORE / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["optional-dependencies"]["mcp"], ["mcp==2.2.0"])
        self.assertEqual(mcp_server.MCP_SDK_REQUIREMENT, "mcp==2.2.0")
        locked = {
            line.strip().lower()
            for line in (CORE / "requirements-mcp.lock").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.assertIn("mcp==2.2.0", locked)

    def test_core_cli_import_does_not_import_optional_sdk(self):
        probe = """
import sys
sys.path.insert(0, {source!r})
import devsquad.cli
assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)
""".format(source=str(CORE / "src"))
        subprocess.run([sys.executable, "-P", "-c", probe], check=True)

    def test_mcp_serve_dispatches_without_writing_protocol_stdout(self):
        runtime = Path("/tmp/devsquad-mcp-boundary")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(mcp_server, "serve_stdio") as serve:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["mcp", "serve", "--runtime-dir", str(runtime)])
        self.assertEqual((code, stdout.getvalue(), stderr.getvalue()), (0, "", ""))
        serve.assert_called_once_with(runtime)

    def test_missing_sdk_is_actionable_and_keeps_stdout_clean(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        missing = mcp_server.MCPDependencyUnavailable("install devsquad-core[mcp]")
        with mock.patch.object(mcp_server, "serve_stdio", side_effect=missing):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["mcp", "serve"])
        self.assertEqual(code, 69)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("devsquad-core[mcp]", stderr.getvalue())


class InstalledWheelMCPBoundaryTest(unittest.TestCase):
    @staticmethod
    def build_python():
        candidates = [
            os.environ.get("DEVSQUAD_BUILD_PYTHON"),
            sys.executable,
            str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"),
            shutil.which("python3.13"),
            shutil.which("python3.12"),
            shutil.which("python3.11"),
        ]
        for candidate in dict.fromkeys(value for value in candidates if value):
            result = subprocess.run(
                [candidate, "-c", "import setuptools, wheel; assert int(setuptools.__version__.split('.')[0]) >= 68"],
                text=True,
                capture_output=True,
            )
            if result.returncode == 0:
                return candidate
        return None

    def test_plain_installed_wheel_keeps_cli_usable_without_mcp(self):
        build_python = self.build_python()
        if build_python is None:
            self.skipTest("offline wheel gate requires setuptools>=68 and wheel")
        with tempfile.TemporaryDirectory(prefix="devsquad-mcp-wheel-") as directory:
            root = Path(directory)
            source = root / "core"
            shutil.copytree(CORE, source)
            wheels = root / "wheels"
            wheels.mkdir()
            subprocess.run(
                [
                    build_python, "-m", "pip", "wheel", str(source),
                    "--wheel-dir", str(wheels), "--no-index", "--no-deps", "--no-build-isolation",
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            wheel = next(wheels.glob("devsquad_core-*.whl"))
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            venv = root / "venv"
            subprocess.run([build_python, "-m", "venv", str(venv)], check=True, env=environment)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            squad = venv / ("Scripts/squad.exe" if os.name == "nt" else "bin/squad")
            subprocess.run(
                [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                check=True,
                text=True,
                capture_output=True,
                env=environment,
            )
            version = subprocess.run(
                [str(squad), "--version"], check=True, text=True, capture_output=True, env=environment,
            )
            self.assertEqual(version.stdout.strip(), "squad 0.1.0")
            missing = subprocess.run(
                [str(squad), "mcp", "serve"], text=True, capture_output=True, env=environment,
            )
            self.assertEqual(missing.returncode, 69)
            self.assertEqual(missing.stdout, "")
            self.assertIn("devsquad-core[mcp]", missing.stderr)


if __name__ == "__main__":
    unittest.main()
