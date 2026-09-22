"""Strict local MCP host-registration templates."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import string
import subprocess
import sys
import tomllib
from typing import Any, Callable

from .contracts import ContractError

SOURCE_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = (
    SOURCE_ROOT
    if (SOURCE_ROOT / "integrations").is_dir()
    else Path(sys.prefix) / "share" / "devsquad"
)
TEMPLATE_FIELDS = {
    "schema_version",
    "id",
    "display_name",
    "executable_paths",
    "executable_names",
    "server_name",
    "surface",
    "register_argv",
    "remove_argv",
    "inspect_argv",
    "inspect_format",
}
PLACEHOLDERS = {
    "host_executable", "squad_executable", "server_name", "surface",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate integration template key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class IntegrationTemplate:
    id: str
    display_name: str
    executable_paths: tuple[str, ...]
    executable_names: tuple[str, ...]
    server_name: str
    surface: str
    register_argv: tuple[str, ...]
    remove_argv: tuple[str, ...] | None
    inspect_argv: tuple[str, ...]
    inspect_format: str
    path: Path

    @classmethod
    def load(cls, path: Path) -> "IntegrationTemplate":
        try:
            value = json.loads(path.read_text(), object_pairs_hook=_unique_object)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot read integration template {path}: {exc}") from exc
        if not isinstance(value, dict) or set(value) != TEMPLATE_FIELDS:
            fields = set(value) if isinstance(value, dict) else set()
            raise ContractError(
                f"integration template fields differ: {sorted(fields ^ TEMPLATE_FIELDS)}"
            )
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ContractError("unsupported integration template schema_version")
        for field in ("id", "display_name", "server_name", "surface"):
            if not isinstance(value[field], str) or not value[field]:
                raise ContractError(f"integration {field} must be non-empty")
        for field in (
            "executable_paths", "executable_names", "register_argv", "inspect_argv",
        ):
            sequence = value[field]
            allow_empty = field == "executable_paths"
            if (not isinstance(sequence, list) or (not allow_empty and not sequence)
                    or not all(isinstance(item, str) and item for item in sequence)):
                qualifier = "a string array" if allow_empty else "a non-empty string array"
                raise ContractError(f"integration {field} must be {qualifier}")
            if field in {"executable_paths", "executable_names"}:
                if len(set(sequence)) != len(sequence):
                    raise ContractError(f"integration {field} must be unique")
        if not all(Path(item).is_absolute() for item in value["executable_paths"]):
            raise ContractError("integration executable_paths must be absolute")
        remove_argv = value["remove_argv"]
        if (remove_argv is not None
                and (not isinstance(remove_argv, list) or not remove_argv
                     or not all(isinstance(item, str) and item for item in remove_argv))):
            raise ContractError("integration remove_argv must be null or a non-empty string array")
        if value["inspect_format"] not in {"json", "text"}:
            raise ContractError("integration inspect_format is invalid")
        cls._validate_placeholders(value["register_argv"])
        cls._validate_placeholders(value["inspect_argv"])
        if remove_argv is not None:
            cls._validate_placeholders(remove_argv)
        return cls(
            id=value["id"],
            display_name=value["display_name"],
            executable_paths=tuple(value["executable_paths"]),
            executable_names=tuple(value["executable_names"]),
            server_name=value["server_name"],
            surface=value["surface"],
            register_argv=tuple(value["register_argv"]),
            remove_argv=tuple(remove_argv) if remove_argv is not None else None,
            inspect_argv=tuple(value["inspect_argv"]),
            inspect_format=value["inspect_format"],
            path=path,
        )

    @staticmethod
    def _validate_placeholders(argv: list[str]) -> None:
        formatter = string.Formatter()
        referenced = {
            name
            for argument in argv
            for _, name, _, _ in formatter.parse(argument)
            if name is not None
        }
        if referenced - PLACEHOLDERS:
            raise ContractError(
                f"unknown integration placeholders: {sorted(referenced - PLACEHOLDERS)}"
            )

    def _render(
        self,
        argv: tuple[str, ...],
        *,
        host_executable: Path,
        squad_executable: Path | None,
    ) -> tuple[str, ...]:
        try:
            host = host_executable.resolve(strict=True)
        except OSError as exc:
            raise ContractError("host executable must exist") from exc
        if not host.is_file() or not os.access(host, os.X_OK):
            raise ContractError("host executable must be an executable file")
        referenced = {
            name
            for argument in argv
            for _, name, _, _ in string.Formatter().parse(argument)
            if name is not None
        }
        squad = None
        if "squad_executable" in referenced:
            if squad_executable is None:
                raise ContractError("squad executable is not resolved")
            try:
                squad = squad_executable.resolve(strict=True)
            except OSError as exc:
                raise ContractError("squad executable must exist") from exc
            if not squad.is_file() or not os.access(squad, os.X_OK):
                raise ContractError("squad executable must be an executable file")
        values = {
            "host_executable": str(host),
            "squad_executable": str(squad) if squad else "",
            "server_name": self.server_name,
            "surface": self.surface,
        }
        return tuple(argument.format_map(values) for argument in argv)

    def registration_command(
        self, host_executable: Path, squad_executable: Path,
    ) -> tuple[str, ...]:
        return self._render(
            self.register_argv,
            host_executable=host_executable,
            squad_executable=squad_executable,
        )

    def inspection_command(
        self, host_executable: Path, squad_executable: Path | None = None,
    ) -> tuple[str, ...]:
        return self._render(
            self.inspect_argv,
            host_executable=host_executable,
            squad_executable=squad_executable,
        )

    def removal_command(self, host_executable: Path) -> tuple[str, ...] | None:
        if self.remove_argv is None:
            return None
        return self._render(
            self.remove_argv,
            host_executable=host_executable,
            squad_executable=None,
        )


def load_integrations(root: Path | None = None) -> tuple[IntegrationTemplate, ...]:
    integration_root = root or (CORE_ROOT / "integrations")
    templates = tuple(
        IntegrationTemplate.load(path)
        for path in sorted(integration_root.glob("*/registration.json"))
    )
    if not templates:
        raise ContractError("no local MCP integration templates are installed")
    ids = [template.id for template in templates]
    if len(set(ids)) != len(ids):
        raise ContractError("integration template ids must be unique")
    return templates


def resolve_squad_executable(explicit: Path | None = None) -> Path:
    """Resolve one executable that remains valid outside the current shell."""

    candidates = (
        [explicit]
        if explicit is not None
        else [
            Path(found) if (found := shutil.which("squad")) else None,
            CORE_ROOT / "bin" / "squad",
        ]
    )
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    raise ContractError(
        "cannot resolve a stable squad executable; install devsquad-core or "
        "pass --squad-executable"
    )


def _registration(
    *, scope: str, path: Path, value: Any, disabled_key: str | None = None,
) -> dict[str, Any]:
    valid = isinstance(value, dict)
    command = value.get("command") if valid else None
    args = value.get("args", []) if valid else None
    if not isinstance(command, str) or not command:
        valid = False
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        valid = False
    enabled = True
    if isinstance(value, dict):
        if disabled_key is not None:
            enabled = value.get(disabled_key) is not True
        elif "enabled" in value:
            enabled = value.get("enabled") is True
    return {
        "scope": scope,
        "path": str(path),
        "command": command if isinstance(command, str) else None,
        "args": args if isinstance(args, list) else None,
        "enabled": enabled,
        "valid": valid,
    }


def _safe_parse_error(path: Path, format_name: str) -> str:
    return f"cannot parse {format_name} configuration at {path}"


def _json_document(path: Path) -> Any:
    return json.loads(path.read_text(), object_pairs_hook=_unique_object)


def _toml_document(path: Path) -> Any:
    return tomllib.loads(path.read_text())


def registration_sources(
    template: IntegrationTemplate,
    *,
    home: Path,
    project: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Find direct and inherited registrations without returning env/secrets."""

    project = project.resolve()
    candidates: list[tuple[str, Path, str, tuple[str, ...], str | None]] = []
    if template.id == "codex":
        candidates = [
            ("user", home / ".codex/config.toml", "toml", ("mcp_servers",), None),
            ("project", project / ".codex/config.toml", "toml", ("mcp_servers",), None),
        ]
    elif template.id == "grok":
        candidates = [
            ("user", home / ".grok/config.toml", "toml", ("mcp_servers",), None),
            ("project", project / ".grok/config.toml", "toml", ("mcp_servers",), None),
        ]
    elif template.id == "antigravity":
        candidates = [
            ("user", home / ".gemini/config/mcp_config.json", "json", ("mcpServers",), "disabled"),
            ("project", project / ".gemini/config/mcp_config.json", "json", ("mcpServers",), "disabled"),
            ("project", project / ".gemini/settings.json", "json", ("mcpServers",), "disabled"),
        ]
    elif template.id == "claude-code":
        candidates = [
            ("user", home / ".claude.json", "json", ("mcpServers",), None),
            ("project", project / ".mcp.json", "json", ("mcpServers",), None),
            (
                "local",
                home / ".claude.json",
                "json",
                ("projects", str(project), "mcpServers"),
                None,
            ),
        ]
    registrations: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    documents: dict[tuple[Path, str], Any] = {}
    invalid_documents: set[tuple[Path, str]] = set()
    for scope, path, format_name, key_path, disabled_key in candidates:
        if not path.is_file():
            continue
        cache_key = (path, format_name)
        if cache_key in invalid_documents:
            continue
        if cache_key not in documents:
            try:
                documents[cache_key] = (
                    _json_document(path) if format_name == "json" else _toml_document(path)
                )
            except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, ContractError):
                invalid_documents.add(cache_key)
                errors.append({
                    "scope": scope,
                    "path": str(path),
                    "error": _safe_parse_error(path, format_name),
                })
                continue
        value = documents[cache_key]
        for key in key_path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if isinstance(value, dict) and template.server_name in value:
            registrations.append(_registration(
                scope=scope,
                path=path,
                value=value[template.server_name],
                disabled_key=disabled_key,
            ))
    return registrations, errors


def _actual_from_json(template: IntegrationTemplate, stdout: str) -> dict[str, Any] | None:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if template.id == "codex" and isinstance(value, dict):
        transport = value.get("transport")
        if value.get("name") == template.server_name and isinstance(transport, dict):
            return {
                "command": transport.get("command"),
                "args": transport.get("args"),
                "enabled": value.get("enabled") is True,
                "scope": None,
            }
    if template.id == "grok" and isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("name") == template.server_name:
                return {
                    "command": item.get("command"),
                    "args": item.get("args"),
                    "enabled": item.get("enabled") is True,
                    "scope": item.get("scope"),
                }
    return None


def _actual_from_text(template: IntegrationTemplate, stdout: str) -> dict[str, Any] | None:
    if template.id == "claude-code":
        fields: dict[str, str] = {}
        for line in stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip().lower()] = value.strip()
        if template.server_name + ":" not in stdout.lower() or "command" not in fields:
            return None
        try:
            args = shlex.split(fields.get("args", ""))
        except ValueError:
            return None
        return {
            "command": fields["command"],
            "args": args,
            "enabled": not fields.get("status", "").lower().startswith("disabled"),
            "scope": fields.get("scope"),
            "connection": (
                "failed" if "failed" in fields.get("status", "").lower()
                else "connected" if "connected" in fields.get("status", "").lower()
                else "unknown"
            ),
        }
    if template.id == "antigravity":
        for line in stdout.splitlines():
            columns = re.split(r"\s{2,}", line.strip(), maxsplit=3)
            if len(columns) == 4 and columns[0] == template.server_name:
                try:
                    command = shlex.split(columns[3])
                except ValueError:
                    return None
                if not command:
                    return None
                expected_args = ["mcp", "serve", "--surface", template.surface]
                if len(command) > len(expected_args) and command[-len(expected_args):] == expected_args:
                    executable = " ".join(command[:-len(expected_args)])
                    arguments = expected_args
                else:
                    executable = command[0]
                    arguments = command[1:]
                return {
                    "command": executable,
                    "args": arguments,
                    "enabled": columns[2].lower() == "enabled",
                    "scope": None,
                }
    return None


def _sdk_version() -> str | None:
    if importlib.util.find_spec("mcp") is None:
        return None
    try:
        return metadata.version("mcp")
    except metadata.PackageNotFoundError:
        return None


def _redacted_registration(
    registration: dict[str, Any], expected: dict[str, Any],
) -> dict[str, Any]:
    """Expose matching argv, but never echo arbitrary drifted arguments."""

    arguments = registration.get("args")
    args_match = arguments == expected["args"]
    public = {
        key: value
        for key, value in registration.items()
        if key not in {"args"}
    }
    public["args"] = arguments if args_match else None
    public["args_match"] = args_match
    if isinstance(arguments, list):
        canonical = json.dumps(arguments, ensure_ascii=True, separators=(",", ":"))
        public["args_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
        public["args_count"] = len(arguments)
    else:
        public["args_sha256"] = None
        public["args_count"] = None
    return public


class LocalIntegrationManager:
    """Inspect and idempotently register the local stdio server via host CLIs."""

    def __init__(
        self,
        *,
        project: Path,
        home: Path | None = None,
        squad_executable: Path | None = None,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 8,
        mcp_sdk_available: bool | None = None,
        mcp_sdk_version: str | None = None,
    ):
        self.project = project.resolve(strict=True)
        self.home = (home or Path.home()).resolve(strict=True)
        self.launcher_error = None
        try:
            self.squad_executable = resolve_squad_executable(squad_executable)
        except ContractError as exc:
            self.squad_executable = None
            self.launcher_error = str(exc)
        self.which = which
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        detected_version = _sdk_version()
        self.mcp_sdk_version = (
            detected_version if mcp_sdk_version is None else mcp_sdk_version
        )
        self.mcp_sdk_available = (
            detected_version is not None
            if mcp_sdk_available is None else mcp_sdk_available
        )
        self.mcp_sdk_supported = (
            self.mcp_sdk_available and self.mcp_sdk_version == "2.2.0"
        )

    def _host_executable(self, template: IntegrationTemplate) -> Path | None:
        candidates: list[str] = list(template.executable_paths)
        candidates.extend(
            found
            for name in template.executable_names
            if (found := self.which(name)) is not None
        )
        for found in candidates:
            try:
                resolved = Path(found).resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return resolved
        return None

    def _run(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str] | None:
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        try:
            return self.runner(
                command,
                cwd=self.project,
                env=environment,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def inspect(self, template: IntegrationTemplate) -> dict[str, Any]:
        host = self._host_executable(template)
        expected = {
            "command": str(self.squad_executable) if self.squad_executable else None,
            "args": ["mcp", "serve", "--surface", template.surface],
        }
        sources, source_errors = registration_sources(
            template, home=self.home, project=self.project,
        )
        actual = None
        inspection = {"completed": False, "exit_code": None}
        if host is not None:
            command = template.inspection_command(host)
            completed = self._run(command)
            if completed is not None:
                inspection = {
                    "completed": True,
                    "exit_code": completed.returncode,
                }
                if completed.returncode == 0:
                    actual = (
                        _actual_from_json(template, completed.stdout)
                        if template.inspect_format == "json"
                        else _actual_from_text(template, completed.stdout)
                    )
        matches = bool(
            actual
            and actual.get("command") == expected["command"]
            and actual.get("args") == expected["args"]
            and actual.get("enabled") is True
        )
        loaded_matches_source = bool(
            actual
            and len(sources) == 1
            and actual.get("command") == sources[0].get("command")
            and actual.get("args") == sources[0].get("args")
            and actual.get("enabled") == sources[0].get("enabled")
        )
        if host is None:
            status = "unavailable"
        elif source_errors:
            status = "invalid_config"
        elif len(sources) > 1:
            status = "duplicate"
        elif any(not source["valid"] for source in sources):
            status = "invalid_config"
        elif self.squad_executable is None:
            status = "unstable_launcher"
        elif actual is None and sources:
            status = "not_loaded"
        elif actual is None:
            status = "missing"
        elif not sources:
            status = "inherited"
        elif not loaded_matches_source:
            status = "duplicate"
        elif matches:
            status = "matching"
        else:
            status = "drifted"
        public_sources = [
            _redacted_registration(source, expected) for source in sources
        ]
        public_actual = (
            _redacted_registration(actual, expected) if actual is not None else None
        )
        return {
            "id": template.id,
            "display_name": template.display_name,
            "installed": host is not None,
            "host_executable": str(host) if host else None,
            "server_name": template.server_name,
            "status": status,
            "ready": status == "matching" and self.mcp_sdk_supported,
            "mcp_sdk_available": self.mcp_sdk_available,
            "mcp_sdk_supported": self.mcp_sdk_supported,
            "mcp_sdk_version": self.mcp_sdk_version,
            "expected": expected,
            "loaded": public_actual,
            "sources": public_sources,
            "source_errors": source_errors,
            "inspection": inspection,
            "template": str(template.path),
            "launcher_error": self.launcher_error,
        }

    def setup(self, template: IntegrationTemplate, *, dry_run: bool = False) -> dict[str, Any]:
        before = self.inspect(template)
        status = before["status"]
        if not self.mcp_sdk_available:
            return {**before, "action": "blocked_missing_mcp_sdk", "changed": False}
        if not self.mcp_sdk_supported:
            return {**before, "action": "blocked_unsupported_mcp_sdk", "changed": False}
        if status == "unavailable":
            return {**before, "action": "blocked_host_unavailable", "changed": False}
        if status == "unstable_launcher":
            return {**before, "action": "blocked_unstable_launcher", "changed": False}
        if status == "matching":
            return {**before, "action": "unchanged", "changed": False}
        if status in {"duplicate", "inherited", "not_loaded", "invalid_config"}:
            return {**before, "action": f"blocked_{status}", "changed": False}
        if status == "drifted" and before["sources"][0]["scope"] != "user":
            return {**before, "action": "blocked_inherited", "changed": False}
        action = "add" if status == "missing" else "update"
        if dry_run:
            return {**before, "action": f"would_{action}", "changed": False}
        host = Path(before["host_executable"])
        removed = False
        removal_command = template.removal_command(host) if action == "update" else None
        if removal_command is not None:
            removal = self._run(removal_command)
            if removal is None or removal.returncode != 0:
                return {
                    **before,
                    "action": "update_failed",
                    "changed": None,
                    "removal_exit_code": (
                        removal.returncode if removal is not None else None
                    ),
                }
            removed = True
        completed = self._run(
            template.registration_command(host, self.squad_executable)
        )
        if completed is None or completed.returncode != 0:
            return {
                **before,
                "action": f"{action}_failed",
                "changed": True if removed else None,
                "removal_exit_code": 0 if removed else None,
                "registration_exit_code": (
                    completed.returncode if completed is not None else None
                ),
            }
        after = self.inspect(template)
        if not after["ready"]:
            return {
                **after,
                "action": f"{action}_incomplete",
                "changed": True,
                "removal_exit_code": 0 if removed else None,
                "registration_exit_code": completed.returncode,
            }
        return {
            **after,
            "action": "added" if action == "add" else "updated",
            "changed": True,
            "removal_exit_code": 0 if removed else None,
            "registration_exit_code": completed.returncode,
        }
