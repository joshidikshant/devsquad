"""Small M1 command surface: version, doctor, prepare and classify."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .adapters import AdapterManifest, classify_cli, harness_version, prepare_cli
from .contracts import ContractError, envelope, error_payload

SOURCE_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = SOURCE_ROOT if (SOURCE_ROOT / "adapters").is_dir() else Path(sys.prefix) / "share" / "devsquad"


def manifests() -> list[tuple[Path, AdapterManifest]]:
    return [(path, AdapterManifest.load(path)) for path in sorted((CORE_ROOT / "adapters").glob("*/adapter.json"))]


def command_doctor(_: argparse.Namespace) -> tuple[dict, int]:
    rows = []
    for path, manifest in manifests():
        binary = manifest.resolve_binary()
        version = harness_version(binary) if binary else None
        status = "unavailable" if not binary else ("supported" if version in manifest.verified_versions else "unverified")
        rows.append({"adapter": manifest.name, "transport": manifest.transport, "status": status, "binary": binary, "version": version, "manifest": str(path)})
    ready = any(row["status"] != "unavailable" for row in rows)
    return envelope(data={"core_version": __version__, "ready": ready, "adapters": rows}), 0 if ready else 1


def command_prepare(args: argparse.Namespace) -> dict:
    manifest = AdapterManifest.load(CORE_ROOT / "adapters" / args.adapter / "adapter.json")
    spec = prepare_cli(manifest, prompt=args.prompt, cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout)
    return envelope(data=spec.to_dict()), 0


def command_classify(args: argparse.Namespace) -> dict:
    manifest = AdapterManifest.load(CORE_ROOT / "adapters" / args.adapter / "adapter.json")
    spec = prepare_cli(manifest, prompt="classification", cwd=args.cwd, model=args.model, effort=args.effort, permission=args.permission, timeout_seconds=args.timeout)
    result = classify_cli(spec, returncode=args.returncode, stdout=Path(args.stdout_file).read_text(), stderr=Path(args.stderr_file).read_text())
    return envelope(data=result.to_dict()), 0


class ContractParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContractError(message)


def parser() -> argparse.ArgumentParser:
    p = ContractParser(prog="squad")
    p.add_argument("--version", action="version", version=f"squad {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor"); doctor.add_argument("--json", action="store_true"); doctor.set_defaults(func=command_doctor)
    for name, fn in (("prepare", command_prepare), ("classify", command_classify)):
        cmd = sub.add_parser(name)
        cmd.add_argument("adapter", choices=("codex", "antigravity", "grok"))
        cmd.add_argument("--cwd", default=str(Path.cwd()))
        cmd.add_argument("--model")
        cmd.add_argument("--effort")
        cmd.add_argument("--permission", choices=("read_only", "workspace_write"), default="read_only")
        cmd.add_argument("--timeout", type=int, default=90)
        if name == "prepare":
            cmd.add_argument("--prompt", required=True)
        else:
            cmd.add_argument("--returncode", type=int, required=True)
            cmd.add_argument("--stdout-file", required=True)
            cmd.add_argument("--stderr-file", required=True)
        cmd.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        response, code = args.func(args)
        print(json.dumps(response, sort_keys=True))
        return code
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        print(json.dumps(envelope(error=error_payload(code, str(exc))), sort_keys=True))
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
