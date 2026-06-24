#!/usr/bin/env python3
"""Render session blueprint JSON files to a static HTML site for GitHub Pages."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
SEED_DIR = ROOT / "seed"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"
TEMPLATE_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"

TEMPLATE_FILE = SEED_DIR / "strict-master-output-template.json"
SYLLABUS_FILE = SEED_DIR / "global-speaker-syllabus.json"

SECTION_CSS = {
    "IV": "drill",
    "V": "metrics",
    "VII": "coaching",
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "session"


def session_filename(level: int, chapter_number: int, chapter_title: str) -> str:
    return f"session-{chapter_number}-{slugify(chapter_title)}.html"


def session_href_from_root(level: int, chapter_number: int, chapter_title: str) -> str:
    return f"level-{level}/{session_filename(level, chapter_number, chapter_title)}"


def build_field_labels(template: dict) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    for section in template["sections"]:
        labels[section["id"]] = {
            field["key"]: field["label"] for field in section.get("fields", [])
        }
    return labels


def format_generated_at(iso_timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y %b %d, %H:%M UTC")
    except ValueError:
        return iso_timestamp


def prepare_rendered_sections(
    blueprint: dict,
    template: dict,
    field_labels: dict[str, dict[str, str]],
) -> list[dict]:
    sections: list[dict] = []
    blueprint_sections = blueprint.get("sections", {})

    for section_def in template["sections"]:
        section_id = section_def["id"]
        content = blueprint_sections.get(section_id, {})
        title = content.get("title", section_def["title"])
        guidance = content.get("guidance") or section_def.get("guidance")
        time_allocation = section_def.get("time_allocation")

        entry: dict = {
            "id": section_id,
            "title": title,
            "guidance": guidance,
            "time_allocation": time_allocation if section_id == "IV" else None,
            "css_class": SECTION_CSS.get(section_id, "default"),
            "fields": [],
            "checklist": [],
        }

        if section_id == "I":
            objective = content.get("guidance", "")
            if objective:
                entry["guidance"] = objective
                entry["fields"] = []
        elif section_id == "V":
            labels = field_labels[section_id]
            for key in ("technical_accuracy", "bimodal_fluency", "executive_presence"):
                if key in content:
                    entry["checklist"].append(
                        {"label": labels[key], "value": content[key]}
                    )
            entry["fields"] = []
        else:
            labels = field_labels.get(section_id, {})
            for field_def in section_def.get("fields", []):
                key = field_def["key"]
                if key not in content:
                    continue
                value = content[key]
                if key == "key_linguistic_triggers":
                    entry["fields"].append(
                        {
                            "label": labels[key],
                            "value": value,
                            "is_list": True,
                        }
                    )
                else:
                    entry["fields"].append(
                        {
                            "label": labels.get(key, field_def["label"]),
                            "value": value,
                            "is_list": False,
                        }
                    )

        sections.append(entry)

    return sections


def discover_blueprints(paths: list[Path] | None = None) -> list[Path]:
    if paths:
        return sorted(paths)
    if not OUTPUT_DIR.exists():
        return []
    return sorted(OUTPUT_DIR.glob("*.json"))


def build_session_registry(blueprint_files: list[Path]) -> dict[tuple[int, int], dict]:
    registry: dict[tuple[int, int], dict] = {}
    for path in blueprint_files:
        data = load_json(path)
        session = data["session"]
        key = (session["level"], session["chapter_number"])
        registry[key] = {
            "data": data,
            "source": path,
            "href": session_href_from_root(
                session["level"],
                session["chapter_number"],
                session["chapter_title"],
            ),
        }
    return registry


def make_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )


def render_session_page(
    env: Environment,
    data: dict,
    template: dict,
    field_labels: dict[str, dict[str, str]],
    *,
    asset_prefix: str,
) -> str:
    session = data["session"]
    header = data["blueprint"]["header"]
    rendered_sections = prepare_rendered_sections(
        data["blueprint"], template, field_labels
    )

    return env.get_template("session.html").render(
        asset_prefix=asset_prefix,
        home_href=f"{asset_prefix}index.html",
        breadcrumbs=[
            {"label": "Home", "href": f"{asset_prefix}index.html"},
            {
                "label": f"Level {session['level']}",
                "href": f"{asset_prefix}level-{session['level']}/index.html",
            },
            {"label": header["chapter_title"], "href": None},
        ],
        session=session,
        header=header,
        generated_at_display=format_generated_at(data.get("generated_at", "")),
        rendered_sections=rendered_sections,
    )


def build_level_sessions(
    level: dict,
    registry: dict[tuple[int, int], dict],
) -> list[dict]:
    entries = []
    for chapter in level["chapters"]:
        key = (level["level"], chapter["chapter_number"])
        reg = registry.get(key)
        entries.append(
            {
                "chapter_number": chapter["chapter_number"],
                "global_chapter_number": chapter["global_chapter_number"],
                "title": chapter["title"],
                "key_ability": chapter["key_ability"],
                "drill_name": chapter["work_ready_drill"]["name"],
                "href": (
                    session_filename(
                        level["level"],
                        chapter["chapter_number"],
                        chapter["title"],
                    )
                    if reg
                    else None
                ),
            }
        )
    return entries


def copy_assets(site_dir: Path) -> None:
    dest = site_dir / "assets"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ASSETS_DIR / "style.css", dest / "style.css")


def clean_site_dir(site_dir: Path) -> None:
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)


def render_site(
    blueprint_files: list[Path],
    *,
    site_dir: Path = SITE_DIR,
) -> int:
    if not TEMPLATE_FILE.exists() or not SYLLABUS_FILE.exists():
        print("Error: missing seed files in seed/", file=sys.stderr)
        return 1

    template = load_json(TEMPLATE_FILE)
    syllabus = load_json(SYLLABUS_FILE)
    field_labels = build_field_labels(template)
    registry = build_session_registry(blueprint_files)
    env = make_jinja_env()

    clean_site_dir(site_dir)
    copy_assets(site_dir)

    rendered_count = 0
    for (_level_num, _chapter_num), entry in registry.items():
        session = entry["data"]["session"]
        level_dir = site_dir / f"level-{session['level']}"
        level_dir.mkdir(parents=True, exist_ok=True)

        html = render_session_page(
            env,
            entry["data"],
            template,
            field_labels,
            asset_prefix="../",
        )
        out_path = level_dir / session_filename(
            session["level"],
            session["chapter_number"],
            session["chapter_title"],
        )
        out_path.write_text(html, encoding="utf-8")
        rendered_count += 1

    all_sessions: list[dict] = []
    levels_summary: list[dict] = []

    for level in syllabus["levels"]:
        sessions = build_level_sessions(level, registry)
        generated_count = sum(1 for s in sessions if s["href"])

        level_dir = site_dir / f"level-{level['level']}"
        level_dir.mkdir(parents=True, exist_ok=True)

        level_html = env.get_template("level_index.html").render(
            asset_prefix="../",
            home_href="../index.html",
            breadcrumbs=[
                {"label": "Home", "href": "../index.html"},
                {"label": f"Level {level['level']}", "href": None},
            ],
            level=level,
            sessions=sessions,
        )
        (level_dir / "index.html").write_text(level_html, encoding="utf-8")

        levels_summary.append(
            {
                "level": level["level"],
                "title": level["title"],
                "focus": level["focus"],
                "index_href": f"level-{level['level']}/index.html",
                "generated_count": generated_count,
                "total_count": len(sessions),
            }
        )

        for session_entry in sessions:
            all_sessions.append(
                {
                    "global_chapter_number": session_entry["global_chapter_number"],
                    "level": level["level"],
                    "title": session_entry["title"],
                    "drill_name": session_entry["drill_name"],
                    "href": (
                        session_href_from_root(
                            level["level"],
                            session_entry["chapter_number"],
                            session_entry["title"],
                        )
                        if session_entry["href"]
                        else None
                    ),
                }
            )

    program_html = env.get_template("program_index.html").render(
        asset_prefix="",
        home_href="index.html",
        breadcrumbs=None,
        syllabus=syllabus,
        levels=levels_summary,
        all_sessions=all_sessions,
    )
    (site_dir / "index.html").write_text(program_html, encoding="utf-8")

    print(f"Rendered {rendered_count} session(s) to {site_dir}")
    return 0


def sync_to_preview_repo(site_dir: Path, preview_path: Path, *, clean: bool = True) -> None:
    if not preview_path.exists():
        raise FileNotFoundError(f"Preview repo path does not exist: {preview_path}")

    if clean:
        for item in preview_path.iterdir():
            if item.name == ".git":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    for item in site_dir.iterdir():
        dest = preview_path / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def git_publish(preview_path: Path, *, commit: bool, push: bool, message: str) -> None:
    if not (preview_path / ".git").exists():
        raise FileNotFoundError(f"Not a git repository: {preview_path}")

    subprocess.run(["git", "add", "-A"], cwd=preview_path, check=True)

    if commit:
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=preview_path,
        )
        if result.returncode == 0:
            print("No changes to commit.")
        else:
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=preview_path,
                check=True,
            )
            print(f"Committed in {preview_path}")

    if push:
        subprocess.run(["git", "push"], cwd=preview_path, check=True)
        print(f"Pushed from {preview_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render session blueprint JSON to static HTML for GitHub Pages."
    )
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        dest="files",
        metavar="PATH",
        help="Render specific JSON file(s) only (default: all output/*.json).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SITE_DIR,
        help=f"Output directory for the static site (default: {SITE_DIR.name}/).",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Copy built site to PREVIEW_REPO_PATH after rendering.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Git commit in the preview repo (requires --publish).",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Git push in the preview repo (requires --publish and --commit).",
    )
    parser.add_argument(
        "--message",
        default="Update session blueprints",
        help="Commit message for --commit.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()

    blueprint_files = discover_blueprints(args.files)
    if args.files and not blueprint_files:
        print("Error: no blueprint files found for --file", file=sys.stderr)
        return 1

    if not blueprint_files:
        print("Warning: no JSON files in output/; building index pages only.")

    result = render_site(blueprint_files, site_dir=args.output)
    if result != 0:
        return result

    if args.publish:
        preview_path_str = os.getenv("PREVIEW_REPO_PATH", "").strip()
        if not preview_path_str:
            print(
                "Error: PREVIEW_REPO_PATH is not set in .env",
                file=sys.stderr,
            )
            return 1
        preview_path = Path(preview_path_str).resolve()
        try:
            sync_to_preview_repo(args.output, preview_path)
            print(f"Published site to {preview_path}")
            if args.commit or args.push:
                git_publish(
                    preview_path,
                    commit=args.commit,
                    push=args.push,
                    message=args.message,
                )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
