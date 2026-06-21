from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any


SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
SHELL_BLOCK_RE = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL)
SCHEMA_RE = re.compile(r"<!--\s*symphony:schema\s*(.*?)-->", re.DOTALL)
PLACEHOLDER_RE = re.compile(r"(<[^>\n]+>|replace-with|path/to|YOUR_|your_|example\.com)")
REQUIRED_SECTIONS = (
    "Summary",
    "Acceptance Criteria",
    "Validation Commands",
    "Touched Areas",
    "Routing",
    "Risk Notes",
    "Complexity",
)


@dataclass(frozen=True)
class IssueFinding:
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class IssueValidationResult:
    ok: bool
    errors: list[IssueFinding]
    warnings: list[IssueFinding]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [finding.__dict__ for finding in self.errors],
            "warnings": [finding.__dict__ for finding in self.warnings],
        }


def validate_issue_file(path: str | Path) -> IssueValidationResult:
    return validate_issue_text(Path(path).read_text())


def validate_issue_text(text: str) -> IssueValidationResult:
    errors: list[IssueFinding] = []
    warnings: list[IssueFinding] = []

    def error(path: str, message: str) -> None:
        errors.append(IssueFinding("error", path, message))

    def warning(path: str, message: str) -> None:
        warnings.append(IssueFinding("warning", path, message))

    sections = parse_sections(text)
    for section in REQUIRED_SECTIONS:
        if not sections.get(section, "").strip():
            error(f"section.{section}", "required section is missing or empty")

    acceptance = sections.get("Acceptance Criteria", "")
    if acceptance and not re.search(r"(?m)^-\s+\[\s\]\s+\S", acceptance):
        error("section.Acceptance Criteria", "must contain unchecked checklist items")

    validation = sections.get("Validation Commands", "")
    command_blocks = SHELL_BLOCK_RE.findall(validation)
    if validation and not command_blocks:
        error("section.Validation Commands", "must contain a bash/sh/shell code block")
    elif command_blocks:
        commands = [
            line.strip()
            for block in command_blocks
            for line in block.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not commands:
            error("section.Validation Commands", "must contain at least one executable command")
        for command in commands:
            if PLACEHOLDER_RE.search(command):
                error("section.Validation Commands", "commands must be concrete, not placeholders")

    touched = sections.get("Touched Areas", "")
    if touched and not re.search(r"(?m)^-\s+`?[^`\n]+`?", touched):
        error("section.Touched Areas", "must list touched files or directories")

    routing = sections.get("Routing", "")
    if routing:
        if not re.search(r"(?m)^lane:\s*\S+", routing):
            error("section.Routing", "must declare lane")
        if not re.search(r"(?m)^kind:\s*\S+", routing):
            error("section.Routing", "must declare kind")

    complexity = sections.get("Complexity", "")
    if complexity and not re.search(r"(?m)^tier:\s*(small|medium|large)\s*$", complexity):
        error("section.Complexity", "must declare tier: small, medium, or large")

    schema_match = SCHEMA_RE.search(text)
    if not schema_match:
        error("symphony_schema", "missing symphony:schema HTML comment")
    else:
        schema = schema_match.group(1)
        if "schema_version: 1" not in schema:
            error("symphony_schema.schema_version", "must declare schema_version: 1")
        if not re.search(r"(?m)^lane:\s*\S+", schema):
            error("symphony_schema.lane", "must declare lane")
        if not re.search(r"(?m)^kind:\s*\S+", schema):
            error("symphony_schema.kind", "must declare kind")
        if "touched_areas:" not in schema:
            error("symphony_schema.touched_areas", "must declare touched_areas")
        if not re.search(r"(?m)^complexity:\s*(small|medium|large)\s*$", schema):
            error("symphony_schema.complexity", "must declare small, medium, or large complexity")

    if PLACEHOLDER_RE.search(text):
        warning("body", "placeholder-like text remains")

    return IssueValidationResult(ok=not errors, errors=errors, warnings=warnings)


def parse_sections(text: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections
