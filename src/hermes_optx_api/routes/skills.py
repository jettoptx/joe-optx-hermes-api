"""Skills API — list and inspect Hermes Agent skills."""

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

from hermes_optx_api.config import settings

router = APIRouter()


def _parse_skill_md(path: Path) -> dict:
    """Parse a SKILL.md file into metadata dict."""
    try:
        content = path.read_text(encoding="utf-8")
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                meta = yaml.safe_load(parts[1]) or {}
                meta["body_preview"] = parts[2].strip()[:200]
                return meta
        return {"name": path.parent.name, "body_preview": content[:200]}
    except Exception:
        return {"name": path.parent.name, "error": "parse_failed"}


def _scan_skills() -> list[dict]:
    """Scan the skills directory for all installed skills."""
    skills_dir = settings.skills_dir
    if not skills_dir.exists():
        return []

    skills = []
    for skill_md in skills_dir.rglob("SKILL.md"):
        meta = _parse_skill_md(skill_md)
        rel_path = skill_md.parent.relative_to(skills_dir)
        parts = rel_path.parts

        skill_entry = {
            "id": str(rel_path),
            "name": meta.get("name", parts[-1] if parts else "unknown"),
            "description": meta.get("description", ""),
            "version": meta.get("version", ""),
            "author": meta.get("author", ""),
            "category": parts[0] if len(parts) > 1 else "",
            "source": "local" if "local" in str(skill_md) else "builtin",
            "path": str(skill_md),
        }
        skills.append(skill_entry)

    return skills


@router.get("/skills")
async def list_skills(category: str = "", search: str = ""):
    """List all installed skills, optionally filtered."""
    skills = _scan_skills()

    if category:
        skills = [s for s in skills if s["category"] == category]

    if search:
        q = search.lower()
        skills = [
            s for s in skills
            if q in s["name"].lower() or q in s.get("description", "").lower()
        ]

    categories = sorted(set(s["category"] for s in skills if s["category"]))

    return {
        "skills": skills,
        "total": len(skills),
        "categories": categories,
    }


@router.get("/skills/{skill_id:path}")
async def get_skill(skill_id: str):
    """Get details for a specific skill."""
    skill_path = settings.skills_dir / skill_id / "SKILL.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    content = skill_path.read_text(encoding="utf-8")
    meta = _parse_skill_md(skill_path)

    return {
        "id": skill_id,
        "content": content,
        **meta,
    }
