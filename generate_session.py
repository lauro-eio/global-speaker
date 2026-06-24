#!/usr/bin/env python3
"""Generate a Global Speaker session blueprint from syllabus seeds via Gemini."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent
SEED_DIR = ROOT / "seed"
OUTPUT_DIR = ROOT / "output"

PERSONA_FILE = SEED_DIR / "system-persona-mandate.json"
TEMPLATE_FILE = SEED_DIR / "strict-master-output-template.json"
SYLLABUS_FILE = SEED_DIR / "global-speaker-syllabus.json"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "session"


def find_level(syllabus: dict, level_number: int) -> dict:
    for level in syllabus["levels"]:
        if level["level"] == level_number:
            return level
    raise ValueError(f"Level {level_number} not found in syllabus.")


def find_chapter(level: dict, session_number: int) -> dict:
    for chapter in level["chapters"]:
        if chapter["chapter_number"] == session_number:
            return chapter
    raise ValueError(
        f"Session {session_number} not found in Level {level['level']} "
        f"({level['title']})."
    )


def list_sessions(level: dict) -> None:
    for chapter in level["chapters"]:
        drill = chapter["work_ready_drill"]["name"]
        print(
            f"  {chapter['chapter_number']}. {chapter['title']} — {drill}"
        )


def prompt_level(syllabus: dict) -> int:
    print("\nSelect level:")
    for level in syllabus["levels"]:
        print(f"  {level['level']}. {level['title']}")
    while True:
        raw = input("Level [1-3]: ").strip()
        if raw in {"1", "2", "3"}:
            return int(raw)
        print("Enter 1, 2, or 3.")


def prompt_session(level: dict) -> int:
    print(f"\nSelect session for Level {level['level']}: {level['title']}")
    list_sessions(level)
    max_session = len(level["chapters"])
    while True:
        raw = input(f"Session [1-{max_session}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= max_session:
            return int(raw)
        print(f"Enter a number between 1 and {max_session}.")


def build_response_schema(template: dict) -> dict:
    """Build a JSON schema for Gemini structured output from the template seed."""
    section_properties: dict = {}
    required_sections: list[str] = []

    for section in template["sections"]:
        section_id = section["id"]
        required_sections.append(section_id)
        properties: dict = {"title": {"type": "string"}}
        section_required: list[str] = ["title"]

        if section.get("guidance"):
            properties["guidance"] = {"type": "string"}
            section_required.append("guidance")

        for field in section.get("fields", []):
            key = field["key"]
            if key == "key_linguistic_triggers":
                properties[key] = {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 3,
                }
            else:
                properties[key] = {"type": "string"}
            section_required.append(key)

        section_properties[section_id] = {
            "type": "object",
            "properties": properties,
            "required": section_required,
        }

    return {
        "type": "object",
        "properties": {
            "header": {
                "type": "object",
                "properties": {
                    "chapter_title": {"type": "string"},
                    "level": {"type": "integer"},
                    "focus": {
                        "type": "string",
                        "enum": ["Negotiation", "Networking", "Both"],
                    },
                },
                "required": ["chapter_title", "level", "focus"],
            },
            "sections": {
                "type": "object",
                "properties": section_properties,
                "required": required_sections,
            },
        },
        "required": ["header", "sections"],
    }


def build_system_prompt(persona: dict, template: dict) -> str:
    philosophies = "\n".join(
        f"- {item['name']}: {item['description']}"
        for item in persona["core_philosophies"]
    )
    requirements = "\n".join(f"- {item}" for item in persona["output_requirements"])
    section_outline = []

    for section in template["sections"]:
        lines = [f"Section {section['id']}: {section['title']}"]
        if section.get("guidance"):
            lines.append(f"  Guidance: {section['guidance']}")
        if section.get("time_allocation"):
            lines.append(f"  Time allocation: {section['time_allocation']}")
        for field in section.get("fields", []):
            lines.append(f"  - {field['label']} ({field['key']})")
        section_outline.append("\n".join(lines))

    return f"""You are the {persona['role']}.

Purpose: {persona['purpose']}
Tone: {persona['tone']}
Module duration: {persona['program']['module_duration_minutes']} minutes.

Core philosophies:
{philosophies}

Output requirements:
{requirements}

Produce a complete session blueprint as JSON with:
1. "header" — chapter_title, level (integer), focus (Negotiation | Networking | Both)
2. "sections" — keys I through VIII, each matching this outline:

{chr(10).join(section_outline)}

Rules:
- Fill every field with specific, operational, workplace-ready content.
- No placeholder text, bracketed instructions, or generic summaries.
- Section IV must name and expand the syllabus drill; allocate 80% of session time to the drill.
- key_linguistic_triggers must be an array of 2-3 exact phrases.
- Choose focus (Negotiation / Networking / Both) based on the chapter objectives.
"""


def build_user_prompt(
    syllabus: dict,
    level: dict,
    chapter: dict,
) -> str:
    drill = chapter["work_ready_drill"]
    return f"""Generate the session blueprint for this syllabus entry.

Program: {syllabus['title']}
Program tagline: {syllabus['tagline']}

Level: {level['level']} — {level['title']}
Level focus: {level['focus']}

Global chapter number: {chapter['global_chapter_number']}
Session (chapter) number: {chapter['chapter_number']}
Chapter title: {chapter['title']}
Key ability: {chapter['key_ability']}
Work-ready drill: {drill['name']}
Drill description: {drill['description']}

Use the chapter title exactly as given in the header.
Set header.level to {level['level']}.
"""


def validate_blueprint(blueprint: dict, template: dict) -> list[str]:
    errors: list[str] = []

    if "header" not in blueprint:
        errors.append("Missing 'header'.")
    if "sections" not in blueprint:
        errors.append("Missing 'sections'.")
        return errors

    for section in template["sections"]:
        section_id = section["id"]
        if section_id not in blueprint["sections"]:
            errors.append(f"Missing section '{section_id}'.")
            continue

        content = blueprint["sections"][section_id]
        for field in section.get("fields", []):
            key = field["key"]
            if key not in content:
                errors.append(f"Section {section_id} missing field '{key}'.")
            elif key == "key_linguistic_triggers":
                value = content[key]
                if not isinstance(value, list) or not (2 <= len(value) <= 3):
                    errors.append(
                        f"Section {section_id}.{key} must be an array of 2-3 strings."
                    )
            elif not str(content[key]).strip():
                errors.append(f"Section {section_id}.{key} is empty.")

    return errors


def wrap_output(
    blueprint: dict,
    syllabus: dict,
    level: dict,
    chapter: dict,
    model: str,
) -> dict:
    return {
        "id": (
            f"level-{level['level']}-session-{chapter['chapter_number']}-"
            f"{slugify(chapter['title'])}"
        ),
        "template_ref": "strict-master-output-template",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "syllabus_ref": syllabus["id"],
        "session": {
            "global_chapter_number": chapter["global_chapter_number"],
            "level": level["level"],
            "level_title": level["title"],
            "level_focus": level["focus"],
            "chapter_number": chapter["chapter_number"],
            "chapter_title": chapter["title"],
            "key_ability": chapter["key_ability"],
            "work_ready_drill": chapter["work_ready_drill"],
        },
        "blueprint": blueprint,
    }


def generate_blueprint(
    persona: dict,
    template: dict,
    syllabus: dict,
    level_number: int,
    session_number: int,
    *,
    model: str,
    api_key: str,
) -> dict:
    level = find_level(syllabus, level_number)
    chapter = find_chapter(level, session_number)

    client = genai.Client(api_key=api_key)
    schema = build_response_schema(template)

    response = client.models.generate_content(
        model=model,
        contents=build_user_prompt(syllabus, level, chapter),
        config=types.GenerateContentConfig(
            system_instruction=build_system_prompt(persona, template),
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.7,
        ),
    )

    raw = response.text
    if not raw:
        raise RuntimeError("Gemini returned an empty response.")

    blueprint = json.loads(raw)
    errors = validate_blueprint(blueprint, template)
    if errors:
        raise RuntimeError(
            "Generated blueprint failed validation:\n- " + "\n- ".join(errors)
        )

    return wrap_output(blueprint, syllabus, level, chapter, model)


def output_path(level: int, chapter: dict) -> Path:
    filename = (
        f"level-{level}-session-{chapter['chapter_number']}-"
        f"{slugify(chapter['title'])}.json"
    )
    return OUTPUT_DIR / filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Global Speaker session blueprint from the syllabus."
    )
    parser.add_argument(
        "--level",
        type=int,
        choices=[1, 2, 3],
        help="Program level (1-3). Prompts interactively if omitted.",
    )
    parser.add_argument(
        "--session",
        type=int,
        help="Session/chapter number within the level (1-9). Prompts if omitted.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file path (defaults to output/level-N-session-M-*.json).",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if not api_key:
        print("Error: GEMINI_API_KEY is not set in .env", file=sys.stderr)
        return 1

    for path in (PERSONA_FILE, TEMPLATE_FILE, SYLLABUS_FILE):
        if not path.exists():
            print(f"Error: missing seed file {path}", file=sys.stderr)
            return 1

    persona = load_json(PERSONA_FILE)
    template = load_json(TEMPLATE_FILE)
    syllabus = load_json(SYLLABUS_FILE)

    args = parse_args()
    level_number = args.level if args.level is not None else prompt_level(syllabus)
    level = find_level(syllabus, level_number)
    session_number = (
        args.session if args.session is not None else prompt_session(level)
    )

    print(
        f"\nGenerating blueprint for Level {level_number}, "
        f"Session {session_number}..."
    )

    try:
        result = generate_blueprint(
            persona,
            template,
            syllabus,
            level_number,
            session_number,
            model=model,
            api_key=api_key,
        )
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    chapter = find_chapter(level, session_number)
    destination = args.output or output_path(level_number, chapter)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"Saved: {destination}")
    print(f"Chapter: {chapter['title']} (global #{chapter['global_chapter_number']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
