"""Deterministic evidence gates supplemented by independent acceptance review."""

import json
import re
from pathlib import Path

from .registry import git
from .workspace import fingerprint, safe_file

KINDS = {"code", "documentation", "research", "infrastructure", "investigation"}


def validate_spec(kind, spec):
    if kind not in KINDS or not isinstance(spec, dict):
        raise ValueError("Invalid task verification strategy")
    allowed = {"files", "sections", "allowed_paths", "evidence_file", "require_commands", "integration"}
    if set(spec) - allowed:
        raise ValueError("Unknown verification specification field")
    if spec.get("integration", "none") not in {"none", "commit", "merge"}:
        raise ValueError("Invalid required integration")
    for key in ("files", "sections", "allowed_paths"):
        if key in spec and (not isinstance(spec[key], list) or not all(isinstance(x, str) and x for x in spec[key])):
            raise ValueError("Verification specification lists must contain nonempty strings")
    if "require_commands" in spec and not isinstance(spec["require_commands"], bool):
        raise ValueError("require_commands must be boolean")
    if "evidence_file" in spec and not isinstance(spec["evidence_file"], str):
        raise ValueError("evidence_file must be a relative path")
    for path in (
        spec.get("files", [])
        + spec.get("allowed_paths", [])
        + ([spec["evidence_file"]] if spec.get("evidence_file") else [])
    ):
        safe_file(Path("/workspace"), path)


def requires_commands(task):
    spec = json.loads(task.get("verification_spec") or "{}")
    # Code cannot opt out; infrastructure needs actual independent health/state commands.
    return task.get("kind", "code") in {"code", "infrastructure"} or spec.get("require_commands", False)


def changed_files(workspace, baseline):
    return sorted(
        set(
            filter(
                None,
                git(workspace.root, "diff", baseline, "--name-only", "-z").split("\0")
                + git(workspace.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"),
            )
        )
    )


def content_digest(workspace, baseline):
    names = set(filter(None, git(workspace.root, "ls-files", "-z").split("\0"))) | set(
        changed_files(workspace, baseline)
    )
    return {name: fingerprint(safe_file(workspace.root, name)) for name in sorted(names)}


def inspect(task, workspace, baseline, records):
    kind = task.get("kind", "code")
    spec = json.loads(task.get("verification_spec") or "{}")
    validate_spec(kind, spec)
    changed = changed_files(workspace, baseline)
    files = spec.get("files", [])
    if kind != "code" and not files:
        raise ValueError("Task-specific verification blocked: define expected deliverable files")
    texts, evidence = {}, {"kind": kind, "changed_files": changed, "files": {}}
    for name in files:
        item = workspace.read_file(name)
        if not item["content"].strip():
            raise ValueError("Expected deliverable is empty: " + name)
        texts[name], evidence["files"][name] = item["content"], item["sha256"]
    combined = "\n".join(texts.values())
    for section in spec.get("sections", []):
        if section.casefold() not in combined.casefold():
            raise ValueError("Required section is missing: " + section)
    if kind == "documentation":
        allowed = spec.get("allowed_paths") or files
        if any(path not in allowed for path in changed):
            raise ValueError("Documentation task changed unrelated files")
        for name, text in texts.items():
            for target in re.findall(r"\[[^\]]+\]\(([^\s)]+)", text):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue  # Network is unavailable; source review assesses external references.
                target = target.split("#")[0]
                relative = Path(name).parent / target
                # Normalize local parent paths while retaining the workspace boundary.
                resolved = (workspace.root / relative).resolve()
                if (
                    not resolved.is_relative_to(workspace.root.resolve())
                    or not safe_file(workspace.root, str(resolved.relative_to(workspace.root))).exists()
                ):
                    raise ValueError("Broken or escaping documentation reference: " + target)
    if kind == "research":
        sources = re.findall(r"https?://[^\s)>]+", combined)
        if not sources or not re.search(r"uncertaint|limitation", combined, re.I):
            raise ValueError("Research needs sources and documented uncertainty or limitations")
        evidence["sources"] = sources[:100]
    if kind in {"infrastructure", "investigation"}:
        evidence_file = spec.get("evidence_file")
        if not evidence_file:
            raise ValueError("Structured reproducible evidence_file is required")
        observed = json.loads(workspace.read_file(evidence_file)["content"])
        required = (
            ("observations", "health_checks", "rollback")
            if kind == "infrastructure"
            else ("observations", "reproduction", "root_cause_or_uncertainty", "proposed_fix")
        )
        if any(not observed.get(field) for field in required):
            raise ValueError("Evidence lacks " + ", ".join(required))
        evidence["observed"] = observed
        if kind == "infrastructure" and not any(r.get("exit_code") == 0 for r in records):
            raise ValueError(
                "Actual independently observed system/health evidence is unavailable in the configured isolation"
            )
    if requires_commands(task) and not records:
        raise ValueError("Applicable independent commands are required")
    evidence["external_links_checked"] = False
    return evidence
