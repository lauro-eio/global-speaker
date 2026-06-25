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
import markdown

ROOT = Path(__file__).resolve().parent
SEED_DIR = ROOT / "seed"
NOTES_DIR = ROOT / "notes"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"
TEMPLATE_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"

TEMPLATE_FILE = SEED_DIR / "strict-master-output-template.json"
SYLLABUS_FILE = SEED_DIR / "global-speaker-syllabus.json"

FRAMEWORK_DOCS = [
    {
        "slug": "session-framework",
        "title": "Global Speaker Session Framework",
        "source": NOTES_DIR / "Global Speaker Session Framework.md",
        "description": (
            "How coaches run the 90-minute session: timing, rotation, "
            "peer feedback, and the Phase 1–7 delivery flow."
        ),
    },
    {
        "slug": "blueprint-template",
        "title": "The Unified Global Speaker Blueprint Template",
        "source": NOTES_DIR / "The Unified Global Speaker Blueprint Template.md",
        "description": (
            "The per-chapter blueprint structure: module header and "
            "Phase 1–7 fields that every generated session must fill."
        ),
    },
]

PHASE_CSS = {
    "3": "demonstration",
    "4": "drill",
    "7": "coaching",
}


def iter_phases(template: dict) -> list[dict]:
    return template["phases"]


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
    for phase in iter_phases(template):
        phase_labels = {
            field["key"]: field["label"] for field in phase.get("fields", [])
        }
        for metric in phase.get("success_metrics", []):
            phase_labels[metric["key"]] = metric["label"]
        labels[phase["id"]] = phase_labels
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
    blueprint_phases = blueprint.get("phases", blueprint.get("sections", {}))

    for phase_def in iter_phases(template):
        phase_id = phase_def["id"]
        content = blueprint_phases.get(phase_id, {})
        title = content.get("title", phase_def["title"])
        guidance = content.get("guidance") or phase_def.get("guidance")
        time_allocation = phase_def.get("time_allocation")

        entry: dict = {
            "id": phase_id,
            "title": title,
            "guidance": guidance,
            "time_allocation": time_allocation,
            "css_class": PHASE_CSS.get(phase_id, "default"),
            "fields": [],
            "checklist": [],
            "field_groups": [],
        }

        labels = field_labels.get(phase_id, {})
        current_group: str | None = None
        group_fields: list[dict] = []

        for field_def in phase_def.get("fields", []):
            key = field_def["key"]
            if key not in content:
                continue
            field_entry = {
                "label": labels.get(key, field_def["label"]),
                "value": content[key],
                "is_list": False,
                "is_blockquote": key == "coach_script",
            }
            group_name = field_def.get("group")
            if group_name:
                if group_name != current_group:
                    if group_fields:
                        entry["field_groups"].append(
                            {"name": current_group, "fields": group_fields}
                        )
                    current_group = group_name
                    group_fields = []
                group_fields.append(field_entry)
            else:
                entry["fields"].append(field_entry)

        if group_fields and current_group:
            entry["field_groups"].append(
                {"name": current_group, "fields": group_fields}
            )

        for metric in phase_def.get("success_metrics", []):
            key = metric["key"]
            if key in content:
                entry["checklist"].append(
                    {"label": labels[key], "value": content[key]}
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


def layout_context(*, asset_prefix: str) -> dict:
    return {
        "asset_prefix": asset_prefix,
        "home_href": f"{asset_prefix}index.html",
        "framework_href": f"{asset_prefix}framework/index.html",
        "show_nav": True,
    }


def markdown_to_html(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )


def render_framework_pages(env: Environment, site_dir: Path) -> int:
    framework_dir = site_dir / "framework"
    framework_dir.mkdir(parents=True, exist_ok=True)
    asset_prefix = "../"
    layout = layout_context(asset_prefix=asset_prefix)

    index_pages = []
    for doc in FRAMEWORK_DOCS:
        if not doc["source"].exists():
            raise FileNotFoundError(f"Framework source not found: {doc['source']}")

        html_body = markdown_to_html(doc["source"])
        page_html = env.get_template("framework_page.html").render(
            **layout,
            title=doc["title"],
            content=html_body,
            breadcrumbs=[
                {"label": "Home", "href": f"{asset_prefix}index.html"},
                {"label": "Framework", "href": f"{asset_prefix}framework/index.html"},
                {"label": doc["title"], "href": None},
            ],
        )
        out_name = f"{doc['slug']}.html"
        (framework_dir / out_name).write_text(page_html, encoding="utf-8")
        index_pages.append(
            {
                "title": doc["title"],
                "description": doc["description"],
                "href": out_name,
            }
        )

    root_layout = layout_context(asset_prefix="../")
    framework_index = env.get_template("framework_index.html").render(
        **root_layout,
        breadcrumbs=[
            {"label": "Home", "href": "../index.html"},
            {"label": "Framework", "href": None},
        ],
        pages=index_pages,
    )
    (framework_dir / "index.html").write_text(framework_index, encoding="utf-8")
    return len(index_pages)


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
        **layout_context(asset_prefix=asset_prefix),
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
            **layout_context(asset_prefix="../"),
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

    framework_pages = [
        {
            "title": doc["title"],
            "description": doc["description"],
            "href": f"framework/{doc['slug']}.html",
        }
        for doc in FRAMEWORK_DOCS
    ]

    program_html = env.get_template("program_index.html").render(
        **layout_context(asset_prefix=""),
        breadcrumbs=None,
        syllabus=syllabus,
        levels=levels_summary,
        all_sessions=all_sessions,
        framework_pages=framework_pages,
    )
    (site_dir / "index.html").write_text(program_html, encoding="utf-8")

    framework_count = render_framework_pages(env, site_dir)

    print(
        f"Rendered {rendered_count} session(s) and "
        f"{framework_count} framework page(s) to {site_dir}"
    )
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
