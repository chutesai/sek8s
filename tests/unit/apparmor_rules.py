"""A small AppArmor rule reader for the profile tests.

Substring matching on profile text is not good enough and has produced real false
negatives here: a rule inside a comment satisfies `in profile`, `deny capability X,`
satisfies an assertion that X is *granted*, a brace alternation like
`/usr/bin/{rm,realpath} mrix,` is invisible to a check for `/usr/bin/rm `, and
`/usr/local/bin/* mrix,` grants an exec that a literal-path check misses.

Everything here works on parsed rules with comments removed, expands the brace and
glob forms AppArmor actually accepts, and keeps allow and deny apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase

# Permission letters AppArmor mediates on a file rule. There is deliberately no
# chmod/chown/stat here: the kernel's file mask is
# `create read write exec append mmap_exec link lock`, so a rule can never mediate a
# metadata operation, and a test claiming otherwise would be wrong.
EXEC_PERMS = set("xipuPUIC")


@dataclass(frozen=True)
class Rule:
    raw: str
    deny: bool
    kind: str  # "file" | "capability" | "network" | "dbus" | "unix" | "other"
    target: str = ""  # path, capability name, ...
    perms: str = ""
    exec_target: str = ""  # profile named by `cx -> name` / `px -> name`
    profile: str = ""  # which (sub)profile the rule sits in; "" = top level


def _strip_comment(line: str) -> str:
    """Drop a trailing comment. Quotes are not special in AppArmor paths we use."""
    return line.split("#", 1)[0].rstrip()


def expand(path: str) -> list[str]:
    """Expand AppArmor brace alternation into concrete path spellings.

    `/{,usr/}bin/bash` -> `/bin/bash`, `/usr/bin/bash`
    `/usr/bin/{rm,realpath}` -> `/usr/bin/rm`, `/usr/bin/realpath`
    Globs (`*`, `**`) are left in place; matching handles them.
    """
    m = re.search(r"\{([^{}]*)\}", path)
    if not m:
        return [path]
    # Bound to names first: black inserts a space before ':' when a slice bound is a
    # call, which flake8 then flags as E203. No other file in the repo trips it.
    start, end = m.start(), m.end()
    head, tail = path[:start], path[end:]
    out = []
    for alt in m.group(1).split(","):
        out.extend(expand(head + alt + tail))
    return out


def parse(text: str) -> list[Rule]:
    """Parse a profile into rules, tracking which (sub)profile each sits in."""
    rules: list[Rule] = []
    stack: list[str] = []
    for line in text.splitlines():
        body = _strip_comment(line).strip()
        if not body:
            continue

        prof = re.match(r"profile\s+([\w.\-/]+)", body)
        if prof:
            stack.append(prof.group(1))
            continue
        if body.startswith("}"):
            if stack:
                stack.pop()
            continue
        # a top-level `profile name flags=(..) {` already handled; bare `{` is noise
        if body in {"{", "};"}:
            continue

        deny = body.startswith("deny ")
        if deny:
            body = body.removeprefix("deny ").strip()
        # Depth 1 is the profile itself -- its rules are the "parent" scope, "".
        # Only nested sub-profiles get a name, which is what the tests reason about.
        where = stack[-1] if len(stack) > 1 else ""

        cap = re.match(r"capability\s+([\w_]+)\s*,", body)
        if cap:
            rules.append(
                Rule(line.strip(), deny, "capability", cap.group(1), profile=where)
            )
            continue
        if body.startswith("network"):
            rules.append(Rule(line.strip(), deny, "network", body, profile=where))
            continue
        if body.startswith("dbus"):
            rules.append(Rule(line.strip(), deny, "dbus", body, profile=where))
            continue
        if body.startswith("unix"):
            rules.append(Rule(line.strip(), deny, "unix", body, profile=where))
            continue

        # file rule: <path> <perms> [-> target] ,
        f = re.match(
            r"(?:owner\s+)?(/\S*)\s+([a-zA-Z]+)\s*(?:->\s*([\w.]+)\s*)?,", body
        )
        if f:
            rules.append(
                Rule(
                    line.strip(),
                    deny,
                    "file",
                    f.group(1),
                    f.group(2),
                    f.group(3) or "",
                    where,
                )
            )
            continue
        rules.append(Rule(line.strip(), deny, "other", body, profile=where))
    return rules


def _matches(pattern: str, path: str) -> bool:
    for spelling in expand(pattern):
        if spelling == path:
            return True
        # AppArmor ** crosses '/', * does not. fnmatch's * crosses, so approximate:
        # translate ** to a marker fnmatch can cross, and require single * to not.
        if "*" in spelling:
            if "**" in spelling:
                if fnmatchcase(path, spelling.replace("**", "*")):
                    return True
            elif fnmatchcase(path, spelling) and path.count("/") == spelling.count("/"):
                return True
    return False


def grants(rules: list[Rule], path: str, perms: str = "", *, profile: str = "") -> bool:
    """True if some ALLOW rule in `profile` covers `path` with all of `perms`.

    An explicit deny anywhere in the same profile wins, as it does in the kernel.
    """
    scoped = [r for r in rules if r.kind == "file" and r.profile == profile]
    if any(r.deny and _matches(r.target, path) for r in scoped):
        return False
    for r in scoped:
        if r.deny or not _matches(r.target, path):
            continue
        if all(p in r.perms for p in perms):
            return True
    return False


def exec_rules(rules: list[Rule], *, profile: str = "") -> list[Rule]:
    """Every ALLOW rule in `profile` that permits an exec, in any exec mode."""
    return [
        r
        for r in rules
        if r.kind == "file"
        and not r.deny
        and r.profile == profile
        and EXEC_PERMS & set(r.perms)
    ]


def can_exec(rules: list[Rule], path: str, *, profile: str = "") -> bool:
    return any(_matches(r.target, path) for r in exec_rules(rules, profile=profile))


def capabilities(rules: list[Rule], *, profile: str = "", denied: bool = False) -> set:
    """Capability names granted (or denied) in `profile`. Keeps the two apart, so an
    inverted `deny capability setuid,` can never satisfy a grant assertion."""
    return {
        r.target
        for r in rules
        if r.kind == "capability" and r.profile == profile and r.deny == denied
    }


def profiles_in(text: str) -> set:
    """Names of the nested sub-profiles only. The outermost profile is the parent scope
    and is addressed as "" so callers do not have to know the file's own name."""
    names = re.findall(r"^(\s*)profile\s+([\w.\-/]+)", text, re.M)
    return {n for indent, n in names if indent}
