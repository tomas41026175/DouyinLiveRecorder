"""
tools/upstream_file_sync.py - Bring upstream changes of AUTO_UPDATE_FILES
(src/spider.py ...) into this fork as a commit, for .github/workflows/
upstream-spider-sync.yml. Stdlib only; needs `git` on PATH.

Why a 3-way merge instead of "copy upstream's file over ours":
  This fork carries small patches on top of upstream's spider.py (e.g. the
  CREATE_NO_WINDOW flag for the migu `node` subprocess). A plain copy would
  revert them, and "our file != upstream's file" would stay true forever, so
  a naive sync would open the same PR every day. Instead we remember which
  upstream commit our copy is based on (.github/upstream-sync.json) and:
    * upstream blob == recorded base blob  -> nothing new upstream, skip
    * otherwise `git merge-file ours base theirs`
        - clean  -> write the result, move the baseline to the new commit
        - conflict -> leave the file alone, report "conflict" (human fixes it)
  The merged file is then sanity-checked (compiles; every `from .x import y`
  still resolves inside src/) -- upstream sometimes adds a helper to
  utils.py in the same commit, and the fork does not take utils.py
  automatically. A failed check still produces the commit/PR (so a human can
  look), but marks it "not safe to auto-merge".

CLI (run from repo root):
  python tools/upstream_file_sync.py apply --upstream-ref upstream/main \
      --upstream-url https://github.com/ihmily/DouyinLiveRecorder \
      --json-out plan.json --summary-out plan.md
  python tools/upstream_file_sync.py status-line --status-md STATUS.md \
      --line "- 2026-10-24：..."
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

STATE_PATH = ".github/upstream-sync.json"
CHECKER_PATH = "src/update_checker.py"
PACKAGE_DIR = "src"
STATUS_HEADING = "## 目前進度"

# per-file statuses
UNCHANGED = "unchanged"          # upstream did not touch the file since baseline
MERGED = "merged"                # clean 3-way merge, file rewritten
CONFLICT = "conflict"            # merge conflict, file untouched
NO_BASELINE = "no_baseline"      # file not in state file, cannot 3-way merge
MISSING_UPSTREAM = "missing_upstream"  # upstream deleted/renamed the file


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def read_auto_update_files(checker_source: str) -> List[str]:
    """AUTO_UPDATE_FILES from update_checker.py, read with ast (importing the
    module would need `requests`). Raises ValueError if missing/not a list of
    strings, so the workflow fails loudly instead of syncing nothing."""
    tree = ast.parse(checker_source)
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        if "AUTO_UPDATE_FILES" in targets:
            files = ast.literal_eval(value)
            if not isinstance(files, (list, tuple)) or not all(
                    isinstance(f, str) and f for f in files):
                raise ValueError("AUTO_UPDATE_FILES must be a list of non-empty strings")
            return list(files)
    raise ValueError("AUTO_UPDATE_FILES not found")


def _top_level_names(source: str) -> Optional[set]:
    """Names bound at module top level; None if unknowable (star import /
    syntax error) -- callers treat None as 'cannot verify, assume fine'."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == "*":
                    return None
                names.add(a.asname or a.name.split(".")[0])
    return names


def missing_relative_imports(source: str, package_dir: Path) -> List[str]:
    """For a module that lives directly in `package_dir`, list every
    `from .mod import name` / `from . import name` that would not resolve.
    Only level-1 relative imports are checked (that is all spider.py uses)."""
    tree = ast.parse(source)
    problems: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        mod_parts = node.module.split(".") if node.module else []
        base = package_dir.joinpath(*mod_parts)
        if mod_parts:
            if (base.with_suffix(".py")).is_file():
                mod_file = base.with_suffix(".py")
            elif (base / "__init__.py").is_file():
                mod_file = base / "__init__.py"
            else:
                problems.append(f"from .{node.module} import ...: module not found")
                continue
        else:
            mod_file = package_dir / "__init__.py"
        names = _top_level_names(mod_file.read_text(encoding="utf-8")) \
            if mod_file.is_file() else set()
        for a in node.names:
            if a.name == "*" or names is None or a.name in names:
                continue
            sub = base / a.name
            if sub.with_suffix(".py").is_file() or (sub / "__init__.py").is_file():
                continue  # `from . import utils` -> submodule
            problems.append(f"from .{node.module or ''} import {a.name}: name not found")
    return problems


def compiles(source: str, filename: str) -> Optional[str]:
    """None if `source` compiles, else the error text."""
    try:
        compile(source, filename, "exec")
        return None
    except SyntaxError as e:
        return f"{type(e).__name__}: {e}"


def insert_status_line(text: str, line: str, heading: str = STATUS_HEADING) -> str:
    """Insert `line` as the first bullet under `heading` (after one blank
    line). Raises ValueError if the heading is missing."""
    lines = text.split("\n")
    try:
        idx = next(i for i, ln in enumerate(lines) if ln.strip() == heading)
    except StopIteration:
        raise ValueError(f"heading {heading!r} not found")
    pos = idx + 1
    if pos < len(lines) and lines[pos].strip() == "":
        pos += 1
    return "\n".join(lines[:pos] + [line] + lines[pos:])


def summarize(results: List[Dict], upstream_commit: str, upstream_url: str) -> Dict:
    """Overall decision from per-file results."""
    changed = any(r["status"] == MERGED for r in results)
    blockers = [r for r in results
                if r["status"] in (CONFLICT, NO_BASELINE, MISSING_UPSTREAM)
                or r.get("checks")]
    return {
        "changed": changed,
        # auto-merge only when something changed AND nothing needs a human
        "auto_merge_ok": changed and not blockers,
        "needs_attention": bool(blockers),
        "upstream_commit": upstream_commit,
        "upstream_short": upstream_commit[:7],
        "upstream_url": upstream_url,
        "files": results,
    }


def render_markdown(plan: Dict) -> str:
    out = [f"上游 commit：[`{plan['upstream_short']}`]"
           f"({plan['upstream_url']}/commit/{plan['upstream_commit']})", "",
           "| 檔案 | 結果 | 說明 |", "|---|---|---|"]
    for r in plan["files"]:
        detail = "; ".join([r.get("detail", "")] + r.get("checks", [])).strip("; ")
        out.append(f"| `{r['path']}` | {r['status']} | {detail or '-'} |")
    out.append("")
    if plan["auto_merge_ok"]:
        out.append("可自動合併：3-way merge 乾淨、可編譯、相對 import 都找得到。")
    elif plan["changed"]:
        out.append("**不自動合併**：有檔案需要人看（衝突／缺基準／import 對不上）。")
    elif plan["needs_attention"]:
        out.append("**沒有可提交的變更，但有檔案需要人處理**（見上表）。")
    else:
        out.append("上游沒有動到清單內的檔案。")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# git-backed apply
# ---------------------------------------------------------------------------
def _git(args: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=False, check=check)


def _git_out(args: List[str], cwd: Path) -> Optional[str]:
    r = _git(args, cwd, check=False)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def load_state(repo: Path) -> Dict:
    p = repo / STATE_PATH
    if not p.is_file():
        return {"files": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("files", {})
    return data


def save_state(repo: Path, state: Dict) -> None:
    p = repo / STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_file(repo: Path, rel: str, upstream_ref: str, upstream_commit: str,
              state: Dict) -> Dict:
    """3-way merge one file in the working tree; updates `state` in place on
    a clean merge. Returns a result dict {path, status, detail, checks}."""
    res = {"path": rel, "status": UNCHANGED, "detail": "", "checks": []}
    theirs_blob = _git_out(["rev-parse", f"{upstream_ref}:{rel}"], repo)
    if not theirs_blob:
        res.update(status=MISSING_UPSTREAM, detail="上游沒有這個檔案（改名或刪除？）")
        return res
    entry = state["files"].get(rel)
    if not entry or not entry.get("base_commit"):
        res.update(status=NO_BASELINE, detail=f"{STATE_PATH} 沒有基準 commit")
        return res
    if entry.get("base_blob") == theirs_blob:
        res["detail"] = f"基準 {entry['base_commit'][:7]} 之後上游沒改"
        return res

    local = repo / rel
    base = _git(["show", f"{entry['base_commit']}:{rel}"], repo).stdout
    theirs = _git(["show", f"{upstream_ref}:{rel}"], repo).stdout
    with tempfile.TemporaryDirectory() as td:
        b, t = Path(td, "base"), Path(td, "theirs")
        b.write_bytes(base)
        t.write_bytes(theirs)
        r = subprocess.run(["git", "merge-file", "-p", str(local), str(b), str(t)],
                           cwd=str(repo), capture_output=True)
    if r.returncode != 0:
        n = r.returncode if r.returncode > 0 else "?"
        res.update(status=CONFLICT, detail=f"與 fork 的修改衝突（{n} 處），檔案未更動")
        return res

    merged = r.stdout
    local.write_bytes(merged)
    text = merged.decode("utf-8", errors="replace")
    err = compiles(text, rel)
    if err:
        res["checks"].append(f"無法編譯：{err}")
    else:
        pkg = repo / Path(rel).parent
        if pkg == repo / PACKAGE_DIR:
            res["checks"].extend(missing_relative_imports(text, pkg))
    state["files"][rel] = {"base_commit": upstream_commit, "base_blob": theirs_blob}
    res.update(status=MERGED,
               detail=f"{entry['base_commit'][:7]} → {upstream_commit[:7]}")
    return res


def apply(repo: Path, upstream_ref: str, upstream_url: str) -> Dict:
    files = read_auto_update_files((repo / CHECKER_PATH).read_text(encoding="utf-8"))
    upstream_commit = _git_out(["rev-parse", f"{upstream_ref}^{{commit}}"], repo)
    if not upstream_commit:
        raise SystemExit(f"cannot resolve {upstream_ref}")
    state = load_state(repo)
    results = [sync_file(repo, rel, upstream_ref, upstream_commit, state) for rel in files]
    plan = summarize(results, upstream_commit, upstream_url)
    if plan["changed"]:
        state["upstream"] = upstream_url
        save_state(repo, state)
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--repo", default=".")
    a.add_argument("--upstream-ref", default="upstream/main")
    a.add_argument("--upstream-url", default="https://github.com/ihmily/DouyinLiveRecorder")
    a.add_argument("--json-out")
    a.add_argument("--summary-out")
    s = sub.add_parser("status-line")
    s.add_argument("--status-md", default="STATUS.md")
    s.add_argument("--line", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "status-line":
        p = Path(args.status_md)
        p.write_text(insert_status_line(p.read_text(encoding="utf-8"), args.line),
                     encoding="utf-8")
        return 0

    plan = apply(Path(args.repo).resolve(), args.upstream_ref, args.upstream_url)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    md = render_markdown(plan)
    if args.summary_out:
        Path(args.summary_out).write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            for k in ("changed", "auto_merge_ok", "needs_attention"):
                f.write(f"{k}={'true' if plan[k] else 'false'}\n")
            f.write(f"upstream_short={plan['upstream_short']}\n")
            f.write(f"upstream_commit={plan['upstream_commit']}\n")
            changed_files = " ".join(r["path"] for r in plan["files"]
                                     if r["status"] == MERGED)
            f.write(f"changed_files={changed_files}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
