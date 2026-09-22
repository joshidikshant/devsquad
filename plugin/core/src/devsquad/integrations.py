"""Strict local MCP host-registration templates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import string
import sys
from typing import Any

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
    "executable_names",
    "server_name",
    "surface",
    "register_argv",
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
    executable_names: tuple[str, ...]
    server_name: str
    surface: str
    register_argv: tuple[str, ...]
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
        for field in ("executable_names", "register_argv", "inspect_argv"):
            sequence = value[field]
            if (not isinstance(sequence, list) or not sequence
                    or not all(isinstance(item, str) and item for item in sequence)):
                raise ContractError(f"integration {field} must be a non-empty string array")
            if field == "executable_names" and len(set(sequence)) != len(sequence):
                raise ContractError("integration executable_names must be unique")
        if value["inspect_format"] not in {"json", "text"}:
            raise ContractError("integration inspect_format is invalid")
        cls._validate_placeholders(value["register_argv"])
        cls._validate_placeholders(value["inspect_argv"])
        return cls(
            id=value["id"],
            display_name=value["display_name"],
            executable_names=tuple(value["executable_names"]),
            server_name=value["server_name"],
            surface=value["surface"],
            register_argv=tuple(value["register_argv"]),
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
        squad_executable: Path,
    ) -> tuple[str, ...]:
        try:
            host = host_executable.resolve(strict=True)
            squad = squad_executable.resolve(strict=True)
        except OSError as exc:
            raise ContractError("integration executables must exist") from exc
        if (not host.is_file() or not squad.is_file()
                or not os.access(host, os.X_OK) or not os.access(squad, os.X_OK)):
            raise ContractError("integration executables must be executable files")
        values = {
            "host_executable": str(host),
            "squad_executable": str(squad),
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
        self, host_executable: Path, squad_executable: Path,
    ) -> tuple[str, ...]:
        return self._render(
            self.inspect_argv,
            host_executable=host_executable,
            squad_executable=squad_executable,
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
