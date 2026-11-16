"""Skills: list, read, write and delete the project skills, and show the composed system prompt of a role."""
from __future__ import annotations

import re

from flask import jsonify, request

from ...core.skills import DEFAULT_DIR, SkillBook

from . import ApiError, ROLES, SKILL_NAME, web_config, bp



def _check_skill_name(name: str) -> None:
    if not SKILL_NAME.match(name):
        raise ApiError("skill names are lowercase letters, digits and dashes")


@bp.get("/skills")
def list_skills():
    cfg = web_config()
    book = SkillBook(cfg.skills_dir)
    project = cfg.skills_dir.resolve()
    out = []
    for s in sorted(book.skills.values(), key=lambda s: (s.order, s.name)):
        source = "project" if project in s.path.resolve().parents else "default"
        out.append({"name": s.name, "description": s.description, "roles": list(s.roles), "order": s.order,
                    "source": source, "read_only": source == "default",
                    "overrides_default": source == "project" and (DEFAULT_DIR / s.name / "SKILL.md").is_file()})
    return jsonify(skills=out, roles=ROLES)


@bp.get("/skills/<name>")
def get_skill(name):
    _check_skill_name(name)
    skill = SkillBook(web_config().skills_dir).skills.get(name)
    if skill is None:
        raise ApiError(f"no skill {name}", 404)
    return jsonify(name=name, text=skill.path.read_text(encoding="utf-8"))


@bp.put("/skills/<name>")
def put_skill(name):
    _check_skill_name(name)
    text = str((request.get_json(silent=True) or {}).get("text") or "")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m or not re.search(r"^roles:\s*\[.+\]\s*$", m.group(1), re.M):
        raise ApiError("a skill starts with front matter (--- ... ---) that has a roles: [...] line")
    named = re.search(r"^name:\s*(\S+)\s*$", m.group(1), re.M)
    if named and named.group(1) != name:
        raise ApiError(f"front matter name '{named.group(1)}' must match '{name}'")
    if len(text) > 50_000:
        raise ApiError("a skill is at most 50 000 characters")
    target = web_config().skills_dir / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return jsonify(name=name, saved=str(target))


@bp.delete("/skills/<name>")
def delete_skill(name):
    _check_skill_name(name)
    target = web_config().skills_dir / name / "SKILL.md"
    if not target.is_file():
        raise ApiError("only project skills can be deleted (default skills come with the package)", 400)
    target.unlink()
    if not any(target.parent.iterdir()):
        target.parent.rmdir()
    return jsonify(name=name, deleted=True)


@bp.get("/prompt")
def prompt():
    role = request.args.get("role", "")
    if role not in ROLES:
        raise ApiError(f"role must be one of: {', '.join(ROLES)}")
    book = SkillBook(web_config().skills_dir)
    text = book.prompt(role)
    return jsonify(role=role, skills=[s.name for s in book.for_role(role)], prompt=text, chars=len(text))


