#!/usr/bin/env python3
"""prompt-builder 결과물 점검기 (lint)

에이전트가 만든 실행용 프롬프트를 사용자에게 보여 주기 전에 점검한다.
결과물은 "목표:", "대상:"처럼 칸이 정해져 있으므로 칸 단위로 확인한다.

원칙: ✗(오류)는 고칠 방법이 분명한 것만 낸다. 애매한 것은 △(경고)로 낸다.
     에이전트가 ✗를 없애려고 멀쩡한 내용을 망가뜨리거나 (확인 필요)를 남발하지 않게 하려는 것이다.

  ✗ 오류
    - 필수 칸(목표·대상·완료 기준·검증·제약·가정)이 없거나 비었거나 템플릿 자리표시자가 남음
    - 대상에 경로가 하나도 없음, 또는 저장소에 없는 경로에 (확인 필요)·(새 파일) 표시가 없음
    - 완료 기준에 확인할 수 없는 표현("잘 동작", "정상적으로", "문제없이", "이상 없이", "올바르게")
  △ 경고
    - 검증 명령이 저장소에서 찾은 후보에 없음 (출처를 붙이면 사라짐)
    - 완료 기준의 약한 표현("제대로", "완벽", "적절", "안정적" 등)
    - 모호한 표현, 제약에 출처 없음, 위험 작업 표현(키워드 기준), 목표가 김, 칸 이름 변형

사용법 (프롬프트는 stdin heredoc으로)
  python3 prompt_lint.py [--root 저장소경로] [--json] <<'PROMPT_BUILDER_EOF'
  목표: ...
  PROMPT_BUILDER_EOF

여러 프롬프트("프롬프트 1/3" 등)를 한 번에 넣으면 "목표:"마다 나눠 각각 점검한다.
종료 코드: 0 오류 없음(경고만 있을 수 있음), 1 오류 있음, 2 입력 오류
"""

import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompt_helper as ph  # noqa: E402

# ─────────────────────────────────────────────────────────────
# 조정 가능한 설정
# ─────────────────────────────────────────────────────────────

REQUIRED = ["목표", "대상", "완료 기준", "검증", "제약", "가정"]
OPTIONAL = ["참고"]

# 칸 이름 변형 → 표준 이름. 4.6은 "완료기준", "검증 방법"처럼 조금씩 바꿔 쓴다
LABEL_VARIANTS = {
    "목표": r"목표",
    "대상": r"대상(?:\s*파일)?",
    "완료 기준": r"완료\s*기준|완료\s*조건",
    "검증": r"검증(?:\s*방법|\s*명령)?",
    "제약": r"제약(?:\s*(?:사항|조건))?",
    "가정": r"가정(?:\s*사항)?",
    "참고": r"참고(?:\s*자료)?",
}

UNVERIFIED_MARK = re.compile(r"\(\s*확인\s*필요\s*\)")
NEW_FILE_MARK = re.compile(r"\(\s*(새\s*(파일|폴더)|새로\s*만듦|새로\s*만들|신규(\s*(파일|폴더))?)\s*\)|[—\-]\s*새\s*(파일|폴더)")
SOURCE_RE = re.compile(r"출처\s*[:：]\s*`?([^\s)`,]+)`?")
PROMPT_HEADER_RE = re.compile(r"^\s*#*\s*\**\s*\[?프롬프트\s*\d+\s*/\s*\d+\s*\]?\**([\s:：—\-.(\[].*)?$")
NONE_MARK = re.compile(r"^\s*\(?\s*(없음|해당\s*없음|요청[^)]*없음)[^)]*\)?\s*$")
SOURCE_MARK = re.compile(r"(\.md|\.ya?ml|\.json|README|AGENTS|CONTRIBUTING|요청|사용자|문서|CI|설정)\s*[—:\-]|출처\s*[:：]|\(\s*요청(\s*원문)?\s*\)")

# 완료 기준 — ✗: 확인할 방법이 없는 것이 분명한 표현
UNOBSERVABLE_STRONG = [
    r"잘\s*(동작|작동|된다|됨|돌아)", r"정상\s*(적으로|동작|작동)",
    r"문제\s*(없이|없음|없다|없는)(?!\s*(이|가|으로|로)?\s*(출력|표시|나온|나와|뜬|뜨))",
    r"이상\s*(없이|없음|없다)", r"올바르게\s*(동작|작동|처리|된다)",
]
# 완료 기준 — △: 맥락에 따라 괜찮을 수도 있는 표현
UNOBSERVABLE_WEAK = [
    r"제대로", r"깔끔", r"완벽", r"적절", r"원활", r"안정적", r"효율적", r"향상",
    r"개선\s*(된다|됨)", r"좋아(진다|짐)", r"버그가\s*(수정|해결)\s*(된다|됨)",
]

# 대상 칸에서 경로로 인정하는 확장자 (그 밖의 "a.b"는 코드 식별자로 본다)
FILE_EXTS = ph.FILE_EXTS

# 실행 위치만 바꾸는 옵션 (명령 비교에서 무시)
LOCATION_OPTS = {"--filter", "-F", "-C", "--dir", "--prefix", "--cwd", "--workspace", "-w"}
JS_RUNNERS = {"npm", "pnpm", "yarn", "bun"}

MAX_GOAL_LEN = 200

# ─────────────────────────────────────────────────────────────
# 파싱
# ─────────────────────────────────────────────────────────────

_ALT = "|".join(f"(?P<g{i}>{p})" for i, p in enumerate(LABEL_VARIANTS.values()))
_NAMES = list(LABEL_VARIANTS.keys())
# 들여쓰지 않은 줄만 칸으로 본다 ("  - 대상: ..."처럼 가정 안의 불릿은 칸이 아님)
LABEL_RE = re.compile(r"^(?:[-*]\s*|#{1,4}\s*)?\**(?:" + _ALT + r")\**\s*(?:[:：]\s*\**|$)\s*(?P<val>.*)$")
CANONICAL_RE = re.compile(r"^(?:[-*]\s*)?(목표|대상|완료 기준|검증|제약|가정|참고)\s*[:：]")


def _label(line):
    """(칸 이름, 값, 원문, 불릿 여부) 또는 None"""
    m = LABEL_RE.match(line)
    if not m:
        return None
    for i, name in enumerate(_NAMES):
        if m.group(f"g{i}"):
            return name, m.group("val").strip().strip("*").strip(), line.strip(), bool(re.match(r"[-*]\s", line))
    return None


# 이만큼 칸이 모인 뒤에 나온 '목표'만 새 프롬프트의 시작으로 본다
# (본문 중간의 '## 목표' 같은 줄이 프롬프트를 쪼개지 않게)
SPLIT_MIN_FIELDS = 4


def split_prompts(text):
    """'목표'가 나올 때마다 프롬프트를 나눈다. 코드 블록 표시(```)와 '프롬프트 1/3' 머리줄은 무시"""
    lines = [l for l in (text or "").splitlines() if not l.strip().startswith("```")]
    blocks, cur = [], []
    for line in lines:
        if PROMPT_HEADER_RE.match(line):          # "프롬프트 2/3 — 화면" 머리줄은 무조건 나누는 곳
            if any(_label(l) for l in cur):
                blocks.append(cur)
            cur = []
            continue
        lab = _label(line)
        if lab and lab[0] == "목표":
            seen = {x[0] for x in map(_label, cur) if x}
            if len(seen) >= SPLIT_MIN_FIELDS:
                blocks.append(cur)
                cur = []
        cur.append(line)
    if cur:
        blocks.append(cur)
    return ["\n".join(b) for b in blocks if any(_label(l) for l in b)]


def parse(prompt):
    """({칸 이름: 값}, [표준과 다르게 쓴 칸 이름 원문])

    이미 나온 칸 이름이 다시 나오면 앞 칸에 이어지는 내용으로 본다(가정 안의 "- 검증: ..." 등).
    """
    fields, variants, current = {}, [], None
    for line in prompt.splitlines():
        lab = _label(line)
        if lab:
            name, val, raw, bulleted = lab
            if name in fields:
                lab = None                      # 이미 나온 칸 이름 → 앞 칸에 이어지는 내용
        if lab:
            current = name
            fields[current] = val
            if not CANONICAL_RE.match(raw.replace("**", "")):
                variants.append((current, raw))
        elif current and line.strip():
            fields[current] = (fields[current] + "\n" + line.strip()).strip()
    return fields, variants


def _items(value):
    """쉼표·줄·목록 항목으로 나눈다"""
    out = []
    for line in value.splitlines():
        line = re.sub(r"^\s*[-*•]\s*|^\s*\d+[.)]\s*", "", line).strip()
        # 괄호·따옴표 안의 쉼표에서는 나누지 않는다 ("Code review, PR review")
        masked = re.sub(r"\"[^\"]*\"|'[^']*'|“[^”]*”|\([^)]*\)", lambda m: m.group(0).replace(",", "\0"), line)
        out += [p.replace("\0", ",").strip() for p in masked.split(",") if p.strip()]
    return out


TEMPLATE_PLACEHOLDERS = [re.escape(m.group(0)) for m in re.finditer(r"\([^)\n]*\)", ph.TEMPLATE)]


def _is_placeholder(value):
    v = value.strip()
    return any(re.fullmatch(p, v) for p in TEMPLATE_PLACEHOLDERS)


def _strip_quotes(text):
    """인용한 화면 문구·코드는 표현 검사에서 뺀다"""
    return re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`|‘[^’]*’|“[^”]*”|「[^」]*」", " ", text)


# ─────────────────────────────────────────────────────────────
# 대상
# ─────────────────────────────────────────────────────────────


def _path_candidates(item, root=None):
    """대상 항목에서 경로만 고른다. 'LoginService.login()' 같은 코드 식별자는 뺀다.
    root가 주어지면 한글 경로·확장자 없는 파일(Dockerfile 등)도 저장소에 있으면 포함한다"""
    out = []
    clean = UNVERIFIED_MARK.sub(" ", NEW_FILE_MARK.sub(" ", item))
    for c in ph._candidates(clean):
        ext = c.rsplit(".", 1)[-1].lower() if "." in Path(c).name else ""
        if "/" in c or ext in FILE_EXTS:
            out.append(c)
    out += [b for b in ph.bare_file_candidates(item, root) if b not in out] if root else []
    if root:
        out += [h for h in ph.hangul_candidates(clean, root, include_missing=True) if h not in out]
    out = [c for c in out if not any(o != c and o.startswith(c) and c.endswith("/") for o in out)]
    return out


def _outside(c):
    return c.startswith("/") or c.startswith("~") or ".." in Path(c).parts


def check_target(target, root):
    errors, warnings = [], []
    items = _items(target)
    any_path = False
    for item in items:
        cands = _path_candidates(item, root)
        if not cands:
            continue
        any_path = True
        for c in [c for c in cands if _outside(c)]:
            errors.append(f"대상 '{c}'은(는) 저장소 밖 경로입니다. 저장소 안 경로로 쓰세요")
        cands = [c for c in cands if not _outside(c)]
        if UNVERIFIED_MARK.search(item) or not cands:
            continue
        new_file = bool(NEW_FILE_MARK.search(item))
        for m in ph.check_paths(" ".join(cands), root):
            if m["exists"] and "/" in m["mention"] and m["found"] != [m["mention"]]:
                if len(m["found"]) == 1:
                    errors.append(f"대상 '{m['mention']}'은(는) 없습니다. 같은 이름의 파일은 '{m['found'][0]}'에 있습니다 — 경로를 고치세요")
                else:
                    warnings.append(f"대상 '{m['mention']}'은(는) 그 경로에 없습니다. 같은 이름: {', '.join(m['found'])}")
                continue
            if m["exists"]:
                if new_file and m["found"] == [m["mention"]]:
                    warnings.append(f"'{m['mention']}'은(는) 이미 있는 파일인데 (새 파일)이 붙어 있습니다")
                elif len(m["found"]) > 1 and m["found"] != [m["mention"]]:
                    warnings.append(f"대상 '{m['mention']}'에 해당하는 파일이 여럿입니다: {', '.join(m['found'])}")
                continue
            if new_file:
                parent = (root / m["mention"]).parent
                if not parent.is_dir():
                    warnings.append(f"새 파일 '{m['mention']}'의 폴더가 아직 없습니다: {parent.relative_to(root) if ph._inside(root, parent) else parent}")
                continue
            errors.append(f"대상 경로가 저장소에 없습니다: {m['mention']} — 경로를 고치거나, 새로 만들 파일이면 "
                          f"'(새 파일)', 확인 못 했으면 '(확인 필요)'를 그 경로 뒤에 붙이세요")
    if not any_path and not UNVERIFIED_MARK.search(target):
        errors.append("대상에 파일·폴더 경로가 없습니다. 경로를 쓰거나 (확인 필요)를 붙이세요")
    return errors, warnings


# ─────────────────────────────────────────────────────────────
# 검증
# ─────────────────────────────────────────────────────────────


def _norm_cmd(cmd):
    """명령 비교용 정규화: 위치 옵션 제거, 'npm run test' = 'npm test'"""
    toks = cmd.split()
    out, skip = [], False
    for i, t in enumerate(toks):
        if skip:
            skip = False
            continue
        if t in LOCATION_OPTS:
            skip = True
            continue
        if re.match(r"--(filter|dir|prefix|cwd|workspace)=", t):
            continue
        out.append(t)
    if len(out) >= 2 and out[0] in JS_RUNNERS and out[1] == "run":
        out.pop(1)
    if out[:1] == ["bun"] and len(out) >= 2 and out[1] == "test":
        pass
    return " ".join(out)


def _script_commands(root, dirs):
    """package.json의 모든 scripts 키를 러너 형식으로 (루트·하위 프로젝트)"""
    cmds = []
    for d in dirs:
        pkg = d / "package.json"
        if not pkg.exists():
            continue
        try:
            data = json.loads(ph._read(pkg) or "{}")
        except ValueError:
            continue
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            continue
        runner = ph._pkg_runner(d).split()[0]
        cmds += [f"{runner} {k}" for k in scripts]
    return cmds


def _project_dirs(root, targets):
    dirs = [root]
    for t in targets:
        p = root / t
        cur = p if p.is_dir() else p.parent
        while ph._inside(root, cur) and cur != root:
            if any((cur / m).exists() for m in ph.PROJECT_MARKERS):
                if cur not in dirs:
                    dirs.append(cur)
                break
            cur = cur.parent
    return dirs


def _cut_korean(p):
    """명령 뒤에 붙은 한국어 설명을 뗀다. 옵션 값(pytest -k 로그인)이나 경로는 남긴다"""
    toks = p.split()
    out = []
    for i, t in enumerate(toks):
        # 한글로만 된 낱말(설명)에서 자른다. 영문이 섞인 낱말("아무거나Task")은 명령 인자로 본다
        if i > 0 and re.fullmatch(r"[가-힣\W_]+", t) and re.match(r"[가-힣]", t) \
                and not (toks[i - 1].startswith("-") and len(toks[i - 1].strip("-—")) > 0):
            break
        out.append(t)
    return " ".join(out)


def _command_parts(value, root):
    """검증 칸에서 [(명령, 출처 경로 또는 None)]을 뽑는다. 출처는 그 명령이 든 조각에만 적용한다"""
    # "(확인 필요) — 저장소에 tests/에는 …" 같은 설명은 명령이 아니다
    text = re.sub(r"\(\s*확인\s*필요\s*\)\s*[—\-:：][^\n]*", " ", value)
    text = UNVERIFIED_MARK.sub(" ", text)
    text = re.sub(r"(^|\s)\d+[.)]\s", "\n", " " + text)         # "1) a 2) b"
    segments = re.split(r"[,\n;]|\s\+\s|&&|→|->", text)
    out = []
    for seg in segments:
        src_m = SOURCE_RE.search(seg)
        src = src_m.group(1) if src_m else None
        seg = SOURCE_RE.sub(" ", seg)
        ticks = re.findall(r"`([^`\n]+)`", seg)
        pieces = ticks if ticks else [seg]
        for p in pieces:
            p = re.sub(r"^\s*[-*•]\s*|^\s*\d+[.)]\s*", "", p).strip()
            p = re.sub(r"^[^A-Za-z./(]*[:：]\s*", "", p)              # "테스트 실행: pnpm test"
            p = re.sub(r"^(Run|run|실행)\s*[:：]\s*", "", p)
            p = re.sub(r"^\(([^)]*)에서\)\s*", "", p)                  # "(apps/web에서) pnpm test"
            p = re.sub(r"^(bash|sh|zsh)\s+(?=\S+\.sh\b)", "", p)     # "bash scripts/check.sh"
            p = re.sub(r"\s*\([^)]*\)\s*$", "", p)                     # 뒤에 붙은 괄호 설명
            p = _cut_korean(p).strip()
            p = re.sub(r"\s*[—\-:：]+$", "", p).strip()               # "pnpm test — 설명", "pnpm test: 설명"
            if not p or re.match(r"^cd\s+\S+$", p) or not re.match(r"[A-Za-z./]", p) \
                    or re.search(r"[가-힣]", p.split()[0]):
                continue
            if " " not in p and not p.startswith("./") and not p.endswith(".sh") and (
                    (root / p).is_file() or ("." in p and p.rsplit(".", 1)[-1].lower() in FILE_EXTS)):
                continue                                            # 명령이 아니라 파일 이름
            out.append((p, src))
    return out


def _source_ok(src, cmd, root):
    """출처 파일이 저장소에 있고, 그 안에 명령이 있는지.
    Makefile은 타깃 이름, package.json은 scripts 키로도 확인한다"""
    if not src:
        return False, ""
    src = re.sub(r":\d+(-\d+)?$", "", src)                     # "ci.yml:10"
    cmd = re.sub(r"\s*[—\-]+\s*$", "", cmd).strip()
    p = root / src
    if _outside(src) or not p.is_file():
        found = ph._walk_find(root, Path(src).name)
        if not found:
            return False, f"출처 '{src}'이(가) 저장소에 없습니다"
        p = root / found[0]
    raw = ph._read(p)
    body = " ".join(raw.replace("\\\n", " ").split())          # 줄 이음(\) 정규화
    if " ".join(cmd.split()) in body or _norm_cmd(cmd) in body:
        return True, ""
    toks = _norm_cmd(cmd).split()
    if p.name == "Makefile" and len(toks) >= 2 and toks[0] == "make":
        if re.search(r"^" + re.escape(toks[1]) + r"\s*:", raw, re.M):
            return True, ""
    if p.name == "package.json" and len(toks) >= 2 and toks[0] in JS_RUNNERS:
        try:
            scripts = json.loads(raw or "{}").get("scripts") or {}
            if isinstance(scripts, dict) and toks[1] in scripts:
                return True, ""
        except (ValueError, AttributeError):
            pass
    return False, f"출처 '{src}'에 '{cmd}'가 없습니다"


def check_verify(verify, target, root):
    warnings = []
    parts = _command_parts(verify, root)
    if UNVERIFIED_MARK.search(verify) and not parts:
        return [], []
    targets = [x for m in ph.check_paths(" ".join(_path_candidates(target, root)), root) for x in m["found"]]
    dirs = _project_dirs(root, targets)
    known = [c["cmd"] for c in ph.detect_verify_commands(root, targets)] + _script_commands(root, dirs)
    known_norm = sorted({_norm_cmd(k) for k in known}, key=len, reverse=True)
    if not parts:
        warnings.append("검증에 실행할 명령이 없습니다. 확인 방법이 없다면 (확인 필요)와 함께 무엇으로 확인할지 쓰세요")
        return [], warnings
    hint = f" (찾은 후보: {', '.join(sorted(set(known)))})" if known else ""
    for cmd, src in parts:
        n = _norm_cmd(cmd)
        exact = n in known_norm
        prefix = next((k for k in known_norm if n.startswith(k + " ")), None)
        first = n.split(" ")[0] if n else ""
        script = ("/" in first or first.startswith(".")) and (root / first).is_file() and ph._inside(root, root / first)
        src_ok, src_msg = _source_ok(src, cmd, root)
        if exact or src_ok or (script and len(n.split()) == 1):
            continue
        if script and not prefix:
            prefix = first                        # "./gradlew 아무거나" → 스크립트 뒤 인자를 지어 붙인 것
        if prefix:
            warnings.append(f"검증 명령 '{cmd}'은(는) 후보 '{prefix}' 뒤에 인자를 붙였습니다. "
                            f"SKILL.md 2단계대로 찾은 명령을 그대로 쓰거나, 인자가 저장소 문서에 있으면 출처를 붙이세요")
            continue
        why = src_msg or "저장소에서 찾은 후보에 없습니다"
        warnings.append(f"검증 명령 '{cmd}': {why}{hint}. 저장소 파일(CI 설정 등)에 있는 명령이면 "
                        f"그 명령 바로 뒤에 '(출처: 그 파일 경로)'를 붙이세요(명령마다 따로)")
    return [], warnings


# ─────────────────────────────────────────────────────────────
# 전체 점검
# ─────────────────────────────────────────────────────────────


def lint_one(prompt, root):
    errors, warnings = [], []
    f, variants = parse(prompt)

    for name, raw in variants:
        warnings.append(f"칸 이름 '{raw.split(':')[0].strip()}'을(를) 표준 이름 '{name}:'으로 쓰세요")

    for name in REQUIRED:
        if name not in f:
            errors.append(f"'{name}:' 칸이 없습니다")
        elif not f[name].strip() or _is_placeholder(f[name]):
            errors.append(f"'{name}:' 칸이 비었거나 템플릿 자리표시자가 남아 있습니다"
                          + (" (쓸 것이 없으면 '없음'이라고 쓰세요)" if name in ("제약", "가정") else ""))

    goal = f.get("목표", "")
    if len(goal) > MAX_GOAL_LEN:
        warnings.append(f"목표가 깁니다({len(goal)}자). 한두 문장으로 줄이세요")

    target = f.get("대상", "")
    if target and not _is_placeholder(target):
        e, w = check_target(target, root)
        errors += e
        warnings += w

    verify = f.get("검증", "")
    if verify and not _is_placeholder(verify):
        e, w = check_verify(verify, target, root)
        errors += e
        warnings += w

    criteria = f.get("완료 기준", "")
    if criteria and not _is_placeholder(criteria):
        for item in [l for l in criteria.splitlines() if l.strip()]:
            item = re.sub(r"^\s*[-*•]\s*|^\s*\d+[.)]\s*", "", item).strip()
            if _is_placeholder(item):
                errors.append(f"완료 기준에 템플릿 자리표시자 '{item}'가 남아 있습니다")
                continue
            plain = _strip_quotes(item)
            strong = ph._matches(UNOBSERVABLE_STRONG, plain)
            weak = ph._matches(UNOBSERVABLE_WEAK, plain)
            if strong:
                errors.append(f"완료 기준 '{item}'은(는) 확인할 수 없는 표현({', '.join(strong)})입니다. "
                              f"눈으로 볼 결과(보이는 문구, 통과하는 명령, 돌려받는 값)로 바꾸세요")
            elif weak:
                warnings.append(f"완료 기준 '{item}'의 '{', '.join(weak)}'는 기준이 흐립니다. 가능하면 관찰할 결과로 바꾸세요")

    vague = ph._matches(ph.VAGUE_PATTERNS, _strip_quotes(goal + "\n" + criteria))
    if vague:
        warnings.append(f"모호한 표현이 남아 있습니다: {', '.join(vague)}")

    constraint = f.get("제약", "")
    if constraint and not _is_placeholder(constraint) and not NONE_MARK.match(constraint):
        for item in _items(constraint):
            if not NONE_MARK.match(item) and not SOURCE_MARK.search(item):
                warnings.append(f"제약 '{item}'에 출처가 없습니다. 출처(예: AGENTS.md —)를 붙이거나, 요청에 없던 것이면 가정으로 옮기세요")

    danger = ph._matches(ph.DANGER_PATTERNS, prompt)
    if danger:
        warnings.append(f"위험 작업 표현이 있습니다({', '.join(danger)}). 키워드 기준이므로, 그 작업을 실제로 "
                        f"실행하는 프롬프트일 때만 사용자에게 작업 이름을 들어 승인받으세요")

    return {"fields": f, "errors": errors, "warnings": warnings}


def lint(text, root):
    root = Path(root).resolve()
    return {"root": str(root), "prompts": [lint_one(p, root) for p in split_prompts(text)]}


def to_text(result):
    out = [f"저장소 루트: {result['root']}"]
    n = len(result["prompts"])
    total_err = 0
    for i, r in enumerate(result["prompts"], 1):
        head = f"[프롬프트 {i}/{n}] " if n > 1 else ""
        total_err += len(r["errors"])
        state = "통과" if not r["errors"] else f"고칠 것 {len(r['errors'])}개"
        if r["warnings"]:
            state += f", 경고 {len(r['warnings'])}개"
        out.append(f"{head}판정: {state}")
        out += [f"  ✗ {e}" for e in r["errors"]]
        out += [f"  △ {w}" for w in r["warnings"]]
    if total_err:
        out.append("→ ✗를 고친 뒤 다시 점검하세요. 점검은 모두 3번까지만 합니다. "
                   "3번째에도 ✗가 남으면 다시 돌리지 말고, 남은 ✗ 줄을 사용자에게 그대로 보여 주세요")
    else:
        out.append("→ 사용자에게 보여 줘도 됩니다")
    if any(w.startswith("검증 명령") for r in result["prompts"] for w in r["warnings"]):
        out.append("→ '검증 명령' △ 줄은 고치지 못했으면 4단계에서 프롬프트 아래 '확인하지 못한 것'에 그대로 붙이세요")
    return "\n".join(out)


def main(argv):
    args = list(argv[1:])
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    explicit_root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            sys.stderr.write("[prompt_lint] --root 뒤에 경로가 없습니다\n")
            return 2
        explicit_root = args[i + 1]
        del args[i:i + 2]

    text = " ".join(args) if args else ("" if sys.stdin.isatty() else sys.stdin.read())
    if not split_prompts(text):
        print("점검할 프롬프트가 없습니다 — '목표: ...', '대상: ...'처럼 '칸 이름:' 형식으로 다시 쓰고 점검하세요")
        return 2
    root = ph.find_root(explicit_root)
    if not root.is_dir():
        sys.stderr.write(f"[prompt_lint] 저장소 루트가 폴더가 아닙니다: {root}\n")
        return 2

    result = lint(text, root)
    print(json.dumps(result, ensure_ascii=False, indent=2) if as_json else to_text(result))
    return 1 if any(r["errors"] for r in result["prompts"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
