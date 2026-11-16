"""Skill and trace commands: skills (list, or a role's system prompt), trace (digest), learn (learned-rules skill)."""
from __future__ import annotations

from pathlib import Path


def register(sub) -> None:
    s = sub.add_parser("skills", help="list skills, or print the system prompt of a role")
    s.add_argument("--skills", default="skills")
    s.add_argument("--role", help="print the composed system prompt of this role")
    s.set_defaults(func=cmd_skills)

    t = sub.add_parser("trace", help="digest of trace.jsonl files")
    t.add_argument("traces", nargs="+", type=Path)
    t.set_defaults(func=cmd_trace)

    lr = sub.add_parser("learn", help="write the learned-rules skill from trace.jsonl files")
    lr.add_argument("traces", nargs="+", type=Path)
    lr.add_argument("--skills", default="skills")
    lr.add_argument("--model", help="also let this (Big) OpenRouter model condense the digest into rules")
    lr.set_defaults(func=cmd_learn)


def cmd_skills(args) -> int:
    from ..core.skills import SkillBook

    book = SkillBook(args.skills)
    if args.role:
        print(book.prompt(args.role))
        return 0
    for s in sorted(book.skills.values(), key=lambda s: (s.order, s.name)):
        print(f"{s.order:3d} {s.name:20s} [{', '.join(s.roles)}] {s.description}  ({s.path})")
    return 0


def cmd_trace(args) -> int:
    from ..core.learn import digest, load, render

    print(render(digest(load(args.traces))))
    return 0


def cmd_learn(args) -> int:
    from ..core.learn import learn, load
    from ..providers import build_runtime

    rt = build_runtime("openrouter", {"default": args.model}, skills_dirs=[args.skills]) if args.model else None
    print(learn(load(args.traces), args.skills, runtime=rt))
    return 0
