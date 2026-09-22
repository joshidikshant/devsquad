"""Read-only readiness reporting shared by CLI and MCP surfaces."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from . import __version__
from .adapters import AdapterManifest, harness_version
from .integrations import (
    LocalIntegrationManager,
    load_integrations,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = (
    SOURCE_ROOT
    if (SOURCE_ROOT / "adapters").is_dir()
    else Path(sys.prefix) / "share" / "devsquad"
)


def _adapter_rows() -> list[dict[str, Any]]:
    rows = []
    for path in sorted((CORE_ROOT / "adapters").glob("*/adapter.json")):
        manifest = AdapterManifest.load(path)
        binary = manifest.resolve_binary()
        version = harness_version(binary) if binary else None
        status = (
            "unavailable" if not binary
            else "supported" if version in manifest.verified_versions
            else "unverified"
        )
        rows.append({
            "adapter": manifest.name,
            "transport": manifest.transport,
            "status": status,
            "binary": binary,
            "version": version,
            "manifest": str(path),
        })
    return rows


def build_doctor_report(
    *,
    project: Path,
    home: Path | None = None,
    squad_executable: Path | None = None,
    manager: LocalIntegrationManager | None = None,
) -> dict[str, Any]:
    """Report provider and installed-app readiness without modifying config."""

    adapters = _adapter_rows()
    integration_manager = manager or LocalIntegrationManager(
        project=project,
        home=home,
        squad_executable=squad_executable,
    )
    local_apps = [
        integration_manager.inspect(template) for template in load_integrations()
    ]
    installed_apps = [row for row in local_apps if row["installed"]]
    adapter_ready = any(row["status"] != "unavailable" for row in adapters)
    local_apps_required = bool(installed_apps)
    local_apps_ready = (
        all(row["ready"] for row in installed_apps)
        if local_apps_required else True
    )
    return {
        "core_version": __version__,
        "ready": adapter_ready and local_apps_ready,
        "adapter_ready": adapter_ready,
        "local_app_access": {
            "required": local_apps_required,
            "ready": local_apps_ready,
            "installed_count": len(installed_apps),
            "configured_count": sum(row["ready"] for row in installed_apps),
            "mcp_sdk_requirement": "mcp==2.2.0",
            "mcp_sdk_available": integration_manager.mcp_sdk_available,
            "mcp_sdk_supported": integration_manager.mcp_sdk_supported,
            "mcp_sdk_version": integration_manager.mcp_sdk_version,
            "squad_executable": (
                str(integration_manager.squad_executable)
                if integration_manager.squad_executable else None
            ),
            "launcher_error": integration_manager.launcher_error,
        },
        "adapters": adapters,
        "local_apps": local_apps,
    }
