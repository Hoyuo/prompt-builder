#!/usr/bin/env python3
"""prompt-builder 결과물 점검기 (lint)

에이전트가 만든 실행용 프롬프트를 사용자에게 보여 주기 전에 점검한다.
결과물은 "요청 원문:", "목표:"처럼 칸이 정해져 있으므로 칸 단위로 확인한다(형식은 SKILL.md 4단계).

원칙: ✗(오류)는 고칠 방법이 분명한 것만 낸다. 애매한 것은 △(경고)로 낸다.
     에이전트가 ✗를 없애려고 멀쩡한 내용을 망가뜨리거나 (확인 필요)를 남발하지 않게 하려는 것이다.

  ✗ 오류
    - 필수 칸(요청 원문·상황·목표·대상·완료 기준·검증·제약·가정·작업 규칙)이 없거나 비었거나
      템플릿 자리표시자가 남음
    - 제약에 SKILL.md의 자리표시자("(요청에 있던 문장 그대로)" 등)가 그대로 남음
    - 대상에 경로가 하나도 없음, 또는 저장소에 없는 경로에 (확인 필요)·(새 파일) 표시가 없음
    - 완료 기준 줄에 '— 확인: …'이 없거나 비었음 ("검증 명령이 통과한다"류 줄은 예외)
    - 완료 기준 줄에 SKILL.md 형식의 자리표시자("(사용자가 보게 될 결과)" 등)가 남음
    - 완료 기준(확인 앞부분)에 확인할 수 없는 표현("잘 동작", "정상적으로", "문제없이", "이상 없이", "올바르게")
    - 작업 규칙이 SKILL.md 형식의 여섯 문장과 다름 (빠지거나 바뀐 줄. 줄을 더한 것은 괜찮음)
  △ 경고
    - 검증 명령이 저장소에서 찾은 후보에 없음 (출처를 붙이면 사라짐)
    - 완료 기준의 약한 표현("제대로", "완벽", "적절", "안정적" 등), 완료 기준이 명령 통과뿐임
    - 상황에 코드 표기(백틱, 경로, 파일 이름, camelCase, snake_case, 호출 표기)
    - 제약의 '요청 —' 항목이 요청 원문에서 찾을 수 없는 말
    - 모호한 표현, 제약에 출처 없음, 위험 작업 표현(키워드 기준, 작업 규칙 칸 제외), 목표가 김, 칸 이름 변형

사용법 (프롬프트는 stdin heredoc으로)
  python3 prompt_lint.py [--root 저장소경로] [--json] <<'PROMPT_BUILDER_EOF'
  목표: ...
  PROMPT_BUILDER_EOF

여러 프롬프트("프롬프트 1/3" 등)를 한 번에 넣으면 머리줄이나 "요청 원문:"마다 나눠 각각 점검한다.
종료 코드: 0 오류 없음(경고만 있을 수 있음), 1 오류 있음, 2 입력 오류

사용자에게 보낼 질문을 점검하려면 --question (stdin에 질문 글. 저장소는 보지 않고 △만 낸다)
  △ 질문이 3개 이상, 코드 표기, 개발 용어, 보기가 없음
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompt_helper as ph  # noqa: E402

# ─────────────────────────────────────────────────────────────
# 조정 가능한 설정
# ─────────────────────────────────────────────────────────────

REQUIRED = ["요청 원문", "상황", "목표", "대상", "완료 기준", "검증", "제약", "가정", "작업 규칙"]
OPTIONAL = ["참고"]

# 칸 이름 변형 → 표준 이름. 모델은 "완료기준", "검증 방법"처럼 조금씩 바꿔 쓴다
LABEL_VARIANTS = {
    "요청 원문": r"요청\s*원문|원\s*요청|요청(?:\s*내용)?",
    "상황": r"상황|배경|이해한\s*내용",
    "목표": r"목표",
    "대상": r"대상(?:\s*파일)?",
    "완료 기준": r"완료\s*기준|완료\s*조건",
    "검증": r"검증(?:\s*방법|\s*명령)?",
    "제약": r"제약(?:\s*(?:사항|조건))?",
    "가정": r"가정(?:\s*사항)?",
    "참고": r"참고(?:\s*자료)?",
    "작업 규칙": r"작업\s*규칙|실행\s*규칙",
}

UNVERIFIED_MARK = re.compile(r"\(\s*확인\s*필요\s*\)")
NEW_FILE_MARK = re.compile(r"\(\s*(새\s*(파일|폴더)|새로\s*만듦|새로\s*만들|신규(\s*(파일|폴더))?)\s*\)|[—\-]\s*새\s*(파일|폴더)")
SOURCE_RE = re.compile(r"출처\s*[:：]\s*`?([^\s)`,]+)`?")
# 머리줄은 들여쓰지 않은 줄이고, "프롬프트 n/m" 뒤에는 꾸밈(콜론·대시·괄호)만 올 수 있다 (뒤에 문장이 이어지면 본문)
PROMPT_HEADER_RE = re.compile(
    r"^(?:#+\s*\**\s*\[?|\**\s*\[)프롬프트\s*\d+\s*/\s*\d+\s*\]?\**.*$"      # '## 프롬프트 1/2', '[프롬프트 1/2] 제목'
    r"|^\**\s*프롬프트\s*\d+\s*/\s*\d+\s*\]?\**(\s*[:：—\-.(\[].*)?\s*$")
NONE_MARK = re.compile(r"^\s*\(?\s*(없음|해당\s*없음|요청[^)]{0,300}없음)[^)]{0,300}\)?\s*$")   # 길이를 묶어 되짚기가 길어지지 않게
SOURCE_MARK = re.compile(r"(\.md|\.ya?ml|\.json|README|AGENTS|CONTRIBUTING|요청|사용자|문서|CI|설정)\s*[—:\-]|출처\s*[:：]|\(\s*요청(\s*원문)?\s*\)")

# 완료 기준 — ✗: 확인할 방법이 없는 것이 분명한 표현
UNOBSERVABLE_STRONG = [
    r"잘\s*(동작|작동|된다|됨|돌아)", r"(?<!비)정상\s*(적으로|동작|작동)",
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
MAX_TARGETS = 3

# SKILL.md 규칙 2의 자리표시자. 제약에 글자 그대로 남아 있으면 ✗ (예시 문구를 그대로 베낀 것)
CONSTRAINT_PLACEHOLDERS = ("(그 문서에 적힌 규칙)", "(요청에 있던 문장 그대로)")

# SKILL.md 4단계 형식·설명의 완료 기준 자리표시자. 완료 기준 줄에 들어 있으면 ✗
CRITERIA_PLACEHOLDERS = ("(사용자가 보게 될 결과)", "(이 기준을 무엇으로 확인하는지)", "(결과 2)", "(확인 방법)",
                         "(테스트 파일 경로)", "(검증 명령)", "(어디에서 무엇을 해 보면 되는지)")

# 완료 기준 줄의 확인 방법 표시 ("… — 확인: 기존 테스트 tests/test_a.py")
CHECK_MARK_RE = re.compile(r"확인\s*(방법)?\s*[:：]")
# "명령이 통과한다"류 — 다른 것을 망가뜨리지 않았다는 줄. 확인 표시가 없어도 되지만 이것만으로는 부족하다
QUANTITY_ABOVE_RE = re.compile(r"(\d|[개번명회건곳])\s*이상")   # "두 개 이상 없다"의 이상은 수량이다
# 통과류 줄. "통과하고 …", "통과한 뒤 …"처럼 뒤에 다른 서술이 이어지면 아니다 (뒤의 괄호 설명은 괜찮다)
PASS_RE = re.compile(r"(검증\s*명령|테스트|빌드|린트|타입\s*검사|typecheck|lint|test|build)[^\n]*"
                     r"통과(?!하고|하며|하면|하면서|해서|하여|한\s*(뒤|다음|후))", re.I)
# 확인에 "테스트를 추가"라고 썼으면 대상에 테스트 파일이 있어야 한다 (어디에 넣을지 정해 두게)
TEST_ADD_RE = re.compile(r"확인\s*(방법)?\s*[:：][^\n]*테스트를?\s*(하나\s*)?(추가|작성|만들)")
TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__", "e2e", "cypress"}
TEST_NAME_RE = re.compile(r"^test_|\.(test|spec|cy)\.|[_\-]test\.|(Test|Tests|Spec)\.[A-Za-z0-9]+$")


def _has_test_path(target):
    """대상에 테스트 파일이 있는지: 폴더 구간이 test류이거나 파일 이름이 테스트 꼴"""
    for tok in re.findall(r"[^\s,()`]+", target):
        parts = LINE_TAIL_RE.sub("", tok).split("/")
        if any(d.lower() in TEST_DIRS for d in parts[:-1]) or TEST_NAME_RE.search(parts[-1]):
            return True
    return False
# "사람이 직접" 확인은 사용자가 화면에서 할 수 있는 일이어야 한다 — 개발 도구를 쓰게 하면 △
DEV_TOOL_RE = re.compile(r"개발자\s*도구|네트워크\s*탭|devtools|network\s*tab|curl", re.I)

# 상황 칸의 코드 표기 (상황은 사용자가 쓰는 말로 쓴다). 따옴표 안 화면 문구는 빼고 본다.
# 한글도 \w에 들므로 영문·숫자로만 본다 ("대문자/소문자", "검색(" 같은 말이 걸리지 않게)
SITUATION_CODE_PATTERNS = [
    r"(?<![A-Za-z0-9_\-./])[A-Za-z0-9_\-]+/[A-Za-z0-9_\-./]+",                       # 경로
    r"(?<![A-Za-z0-9_\-./])[A-Za-z0-9_\-]+\.(?:" + "|".join(sorted(ph.FILE_EXTS)) + r")(?![A-Za-z0-9_])",  # 파일 이름
    r"(?<![A-Za-z0-9_])[a-z]+[A-Z][A-Za-z0-9]*",                                     # camelCase
    r"(?<![A-Za-z0-9_])[a-z]+_[a-z0-9_]+",                                           # snake_case
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*\(",                                    # 호출 표기
]

# 제약의 '요청 —' 항목: 낱말의 앞 2글자가 요청 원문에 있는 비율이 이보다 낮으면 △
REQUEST_WORD_MIN_RATIO = 1 / 3

# 가정이 이보다 많으면 △ — 사용자가 원하는 결과를 대신 정한 것이 많다는 신호
MAX_ASSUMPTIONS = 3

# 목표·완료 기준·가정·상황의 API 주소. 저장소 추적 파일 어디에도 없으면 △ (지어낸 주소 방지)
API_RE = re.compile(r"(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_\-/{}:.]+)")   # 영문만 — 뒤에 붙은 조사는 뺀다
GIT_GREP_TIMEOUT = 5
ROUTE_DIRS = ("pages/", "app/", "routes/", "src/pages/", "src/app/", "src/routes/")   # 파일 경로가 주소가 되는 폴더

# 상황 칸의 개발 용어 (상황은 화면에서 보이는 일로 쓴다). 영문은 낱말 경계, 한글은 부분 일치
TECH_TERMS = [
    ph.L + r"API" + ph.R, r"호출", r"함수", r"컴포넌트", r"렌더링", r"엔드포인트", r"쿼리", r"파라미터",
    r"병렬", r"순차", r"리팩터링", r"리팩토링", r"변수", r"예외", r"타입", r"콜백", r"비동기",
]

# 요청에 없는 서버 연결 — 사용자가 말하지 않은 서버·백엔드·API를 상황·목표·완료 기준·가정이 끌어들이면 △
SERVER_TERM_RE = re.compile(r"서버|백엔드|" + ph.L + r"API" + ph.R, re.I)

# 사용자에게 보낼 질문의 개발 용어 (--question). 상황 칸의 개발 용어에 질문에서 자주 나오는 말을 더한다
QUESTION_TERMS = TECH_TERMS + [r"프런트엔드", r"프론트엔드", r"백엔드", r"호스팅", r"의존성", r"스키마", r"플랫폼"]
MAX_QUESTIONS = 2
QUESTION_LAST_OK = ("→ 점검 끝. 답은 첫 줄에 '먼저 여쭤볼게요'를 쓰고 그다음에 이 질문과 보기만 쓰세요. "
                    "그 앞뒤에 저장소를 살펴본 이야기나 묻는 이유를 쓰지 않습니다")
QUESTION_LAST_WARN = ("→ △를 읽고 맞으면 질문을 고치세요(다시 점검하지 않습니다). 답은 첫 줄에 '먼저 여쭤볼게요'를 쓰고 "
                      "그다음에 질문과 보기만 쓰세요. 그 앞뒤에 저장소를 살펴본 이야기나 묻는 이유를 쓰지 않습니다")

# 항목 전체가 이름 하나인지 볼 때 떼는 꼬리 ("src 폴더", "tests 디렉터리 전체")
DIR_SUFFIX_RE = re.compile(r"\s*(폴더|디렉터리|디렉토리)(\s*(전체|아래))?\s*$")
SINGLE_NAME_RE = re.compile(r"[\w.\-]+(?:/[\w.\-]+)*/?")

# ─────────────────────────────────────────────────────────────
# 파싱
# ─────────────────────────────────────────────────────────────

_ALT = "|".join(f"(?P<g{i}>{p})" for i, p in enumerate(LABEL_VARIANTS.values()))
_NAMES = list(LABEL_VARIANTS.keys())
# 들여쓰지 않은 줄만 칸으로 본다 ("  - 대상: ..."처럼 가정 안의 불릿은 칸이 아님)
LABEL_RE = re.compile(r"^(?:[-*]\s*|#{1,4}\s*)?\**(?:" + _ALT + r")\**\s*(?:[:：]\s*\**|$)\s*(?P<val>.*)$")
CANONICAL_RE = re.compile(r"^(?:[-*]\s*)?(요청 원문|상황|목표|대상|완료 기준|검증|제약|가정|참고|작업 규칙)\s*[:：]")


def _label(line):
    """(칸 이름, 값, 원문, 불릿 여부) 또는 None"""
    m = LABEL_RE.match(line)
    if not m:
        return None
    for i, name in enumerate(_NAMES):
        if m.group(f"g{i}"):
            return name, m.group("val").strip().strip("*").strip(), line.strip(), bool(re.match(r"[-*]\s", line))
    return None


# 이만큼 칸이 모인 뒤에 나온 '요청 원문'(또는 다시 나온 '목표')만 새 프롬프트의 시작으로 본다
# (본문 중간의 '## 목표' 같은 줄이 프롬프트를 쪼개지 않게)
SPLIT_MIN_FIELDS = 4


def split_prompts(text):
    """프롬프트를 나눈다. '프롬프트 1/3' 머리줄은 무조건 나누는 곳이고, 그 밖에는 형식의 첫 칸인
    '요청 원문'이 칸 SPLIT_MIN_FIELDS개가 모인 뒤 다시 나오면 나눈다. 모델이 '요청 원문'을 빠뜨린
    경우를 위해, 이미 나온 '목표'부터 칸이 SPLIT_MIN_FIELDS개 이상 모인 뒤 '목표'가 다시 나와도 나눈다.
    코드 블록 표시(```)는 무시"""
    lines = [l for l in (text or "").splitlines() if not l.strip().startswith("```")]
    blocks, cur = [], []
    for line in lines:
        if PROMPT_HEADER_RE.match(line):          # "프롬프트 2/3 — 화면" 머리줄은 무조건 나누는 곳
            if any(_label(l) for l in cur):
                blocks.append(cur)
            cur = []
            continue
        lab = _label(line)
        if lab and lab[0] in ("요청 원문", "목표"):
            names = [x[0] for x in map(_label, cur) if x]
            if lab[0] == "목표":
                names = names[names.index("목표"):] if "목표" in names else []
            elif "요청 원문" not in names:      # 칸 순서가 바뀐 것이지 새 프롬프트가 아니다
                names = []
            if len(set(names)) >= SPLIT_MIN_FIELDS:
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


# 작업 규칙 문장은 자리표시자가 아니므로 그 앞부분의 괄호만 쓴다
TEMPLATE_PLACEHOLDERS = [re.escape(m.group(0))
                         for m in re.finditer(r"\([^)\n]*\)", ph.TEMPLATE.split("작업 규칙:")[0])]


def _is_placeholder(value):
    v = value.strip()
    return any(re.fullmatch(p, v) for p in TEMPLATE_PLACEHOLDERS)


def _strip_quotes(text):
    """인용한 화면 문구·코드는 표현 검사에서 뺀다"""
    return re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`|‘[^’]*’|“[^”]*”|「[^」]*」", " ", text)


# ─────────────────────────────────────────────────────────────
# 대상
# ─────────────────────────────────────────────────────────────


EMPTY_CALL_RE = re.compile(r"[A-Za-z0-9_.\-]+\(\)")
# 이름(설명) 또는 (설명). 설명 안의 괄호는 한 겹까지
PAREN_RE = re.compile(r"(?P<name>[A-Za-z0-9_.\-/\[\]@$]*)\((?P<inner>(?:[^()]|\([^()]*\))*)\)")
LINE_TAIL_RE = re.compile(r":\d+(-\d+)?$|#L\d+(-L?\d+)?$")
GLOB_RE = re.compile(r"(?<![^\s`,()])[^\s`,()]*[*?][^\s`,()]*")


def _split_globs(item):
    """(glob 표기를 뺀 항목, glob 목록). src/**/*.ts, tests/test_*.py 같은 것"""
    globs = []

    def take(m):
        tok = m.group(0).rstrip(".")
        if re.fullmatch(r"[\w\-./*?\[\]]+", tok) and ("*" in tok or "." in tok or "/" in tok):
            globs.append(tok)
            return " "
        return m.group(0)
    return GLOB_RE.sub(take, ph.drop_long_words(item)), globs


def _glob_regex(pattern):
    """glob → 정규식. **/는 폴더 0개 이상, *·?는 슬래시를 넘지 않는다"""
    out, i = "", 0
    pattern = pattern[2:] if pattern.startswith("./") else pattern
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def _glob_hit(pattern, root):
    """저장소에서 glob에 맞는 파일·폴더가 하나라도 있는지(node_modules 등은 건너뜀). 첫 일치에서 멈춘다"""
    try:
        rx = _glob_regex(pattern)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ph.SKIP_DIRS]
            base = Path(dirpath).relative_to(root).as_posix()
            for name in filenames + dirnames:
                if rx.match(name if base == "." else f"{base}/{name}"):
                    return True
    except (OSError, ValueError, re.error):
        return False
    return False


def _inner_paths(inner, root):
    """괄호 안 설명에서 경로로 볼 것만: 파일 이름으로 끝나는 슬래시 경로이거나 저장소에 실제로 있는 이름"""
    keep = []
    for c in ph._candidates(inner, root) + (ph.existing_candidates(inner, root) if root else []):
        ext = c.rsplit(".", 1)[-1].lower() if "." in Path(c).name else ""
        try:
            there = bool(root) and (root / c).exists()
        except OSError:
            there = False
        if there or ("/" in c and (ext in FILE_EXTS or c.endswith("/"))):
            keep.append(c)
    return " ".join(keep)


def _strip_parens(text, root, inner_out=None):
    """괄호 설명과 호출 표기를 경로 후보에서 뺀다. 괄호 안에서도 경로 모양이거나 저장소에 있는 것은 남긴다
    (inner_out이 있으면 그렇게 남긴 것을 거기에 모은다).
    app/(shop)/cart처럼 구간 전체가 괄호인 라우트 그룹은 경로의 일부라 그대로 둔다"""
    text = EMPTY_CALL_RE.sub(lambda m: m.group(0) if "/" in m.group(0) else " ", text)   # res.json()

    def one(m):
        name, inner = m.group("name"), m.group("inner")
        if name.endswith("/") and text[m.end():m.end() + 1] == "/":
            return m.group(0)
        if name.endswith("]") and "/" not in name:
            name = ""                                 # [검색](notes/search.py) 같은 마크다운 링크
        elif name and "/" not in name:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if not (ext in FILE_EXTS and re.search(r"\s|[가-힣]", inner)):
                return " "                            # login(), res.json(a,b)
        kept = _inner_paths(inner, root)
        if inner_out is not None:
            inner_out.extend(kept.split())
        return f" {name} {kept} "
    return PAREN_RE.sub(one, text)


def _path_candidates(item, root=None, inner_out=None):
    """대상 항목에서 경로만 고른다. 'LoginService.login()' 같은 코드 식별자는 뺀다.
    root가 주어지면 한글 경로·확장자 없는 파일(Dockerfile 등)도 저장소에 있으면 포함한다"""
    out = []
    item = ph.drop_long_words(item)
    clean = UNVERIFIED_MARK.sub(" ", NEW_FILE_MARK.sub(" ", item))
    clean = _strip_parens(clean, root, inner_out)              # 괄호 설명·호출 표기(process.env, res.json())는 경로가 아니다
    for c in ph._candidates(clean, root):
        ext = c.rsplit(".", 1)[-1].lower() if "." in Path(c).name else ""
        if "/" in c or ext in FILE_EXTS:
            out.append(c)
    out += [b for b in ph.bare_file_candidates(clean, root) if b not in out] if root else []
    if root:
        out += [h for h in ph.hangul_candidates(clean, root, include_missing=True) if h not in out]
        out += [e for e in ph.existing_candidates(clean, root) if e not in out]
        name = _single_name(item)
        if name and name not in out:
            try:
                here = (root / name).exists()
            except OSError:
                here = False
            # 항목 전체가 이름 하나일 때만: 있으면 경로, 없어도 점 파일(.env)이나 (새 파일) 표시가 있으면 경로
            if here or name.startswith(".") or NEW_FILE_MARK.search(item):
                out.append(name)
    out = [LINE_TAIL_RE.sub("", c) for c in out]     # notes/search.py:12, a.py#L3-L9
    out = list(dict.fromkeys(c for c in out if c))
    out = [c for c in out if not any(o != c and o.startswith(c) and c.endswith("/") for o in out)]
    return out


def _single_name(item):
    """항목에서 표시·괄호 설명·백틱·'폴더' 꼬리를 떼고 남은 것이 이름 하나면 그 이름, 아니면 None"""
    rest = UNVERIFIED_MARK.sub(" ", NEW_FILE_MARK.sub(" ", item))
    # 띄어 쓴 괄호 설명만 뗀다. 붙여 쓴 괄호(.then())는 호출 표기라 이름 하나로 보지 않는다
    rest = re.sub(r"(^|\s)\([^)]*\)", " ", rest).replace("`", " ").strip()
    rest = DIR_SUFFIX_RE.sub("", rest).strip()
    if (not rest or len(rest) > ph.MAX_PATH_LEN or not SINGLE_NAME_RE.fullmatch(rest)
            or re.fullmatch(r"[\d.]+", rest)):
        return None
    return rest


def _outside(c):
    return c.startswith("/") or c.startswith("~") or ".." in Path(c).parts


def _count_once(cands, root):
    """같은 항목에서 폴더와 그 폴더 안의 파일이 함께 나오면 폴더는 세지 않는다"""
    def holds(d, o):
        try:
            return o != d and (o.startswith(d.rstrip("/") + "/") or (root / d / o).exists())
        except OSError:
            return False
    return [c for c in cands if not ((c.endswith("/") or (root / c).is_dir()) and any(holds(c, o) for o in cands))]


def check_target(target, root):
    errors, warnings = [], []
    items = _items(target)
    any_path = False
    listed = set()
    if ph.drop_long_words(target) != target:
        warnings.append("대상에 200자가 넘는 낱말이 있어 그 부분은 점검하지 않았습니다")
    for item in items:
        item, globs = _split_globs(item)
        for g in globs:
            if g.startswith("/") or ".." in Path(g).parts:
                continue
            any_path = True
            listed.add(g)
            if not _glob_hit(g, root):
                warnings.append(f"대상 '{g}'에 맞는 파일이 없습니다")
        inner = []
        cands = _path_candidates(item, root, inner)
        if not cands:
            continue
        any_path = True
        listed.update(_count_once(cands, root))
        for c in [c for c in cands if _outside(c)]:
            errors.append(f"대상 '{c}'은(는) 저장소 밖 경로입니다. 저장소 안 경로로 쓰세요")
        cands = [c for c in cands if not _outside(c)]
        if UNVERIFIED_MARK.search(item) or not cands:
            continue
        new_file = bool(NEW_FILE_MARK.search(item))
        rows = ph.check_paths(" ".join(cands), root)
        known = {m["mention"] for m in rows}
        for c in cands:                          # check_paths가 모양으로 못 알아본 이름(tests, .env)은 직접 확인
            if c not in known:
                try:
                    here = (root / c).exists()
                except OSError:
                    here = False
                rows.append({"mention": c, "exists": here, "found": [c] if here else []})
        for m in rows:
            if m["exists"] and "/" in m["mention"] and m["found"] != [m["mention"]]:
                if new_file:                    # 같은 이름이 다른 곳에 있어도 새로 만드는 파일이면 그대로 둔다
                    continue
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
            if m["mention"] in inner and any(r["exists"] and r["mention"] not in inner for r in rows):
                warnings.append(f"대상의 괄호 안 '{m['mention']}'은(는) 저장소에 없습니다. 고칠 파일이라면 괄호 밖에 쓰세요")
                continue
            if "/" not in m["mention"] and any(r["exists"] for r in rows):
                # 같은 항목에 있는 경로가 있으면 이름.확장자 하나는 코드 식별자일 수 있다 — ✗ 대신 △
                warnings.append(f"대상의 '{m['mention']}'은(는) 저장소에 없습니다. 파일 이름이라면 경로를 확인하세요")
                continue
            errors.append(f"대상 경로가 저장소에 없습니다: {m['mention']} — 경로를 고치거나, 새로 만들 파일이면 "
                          f"'(새 파일)', 확인 못 했으면 '(확인 필요)'를 그 경로 뒤에 붙이세요")
    if not any_path and not UNVERIFIED_MARK.search(target):
        errors.append("대상에 파일·폴더 경로가 없습니다. 경로를 쓰거나 (확인 필요)를 붙이세요")
    if len(listed) > MAX_TARGETS:
        warnings.append(f"대상이 {len(listed)}개입니다. 사용자가 짚은 범위가 맞는지 보세요. 요청이 고르지 않은 후보를 모두 넣은 것이라면 "
                        f"프롬프트를 보여 주지 않고 어느 것부터 할지 묻습니다(이미 두 차례 물었다면 가장 작은 것 하나만 남기고 "
                        f"나머지는 '넣지 않은 것'에 적습니다)")
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
# 완료 기준 · 작업 규칙 · 상황 · 제약
# ─────────────────────────────────────────────────────────────

BULLET_RE = re.compile(r"^\s*[-*•]\s*|^\s*\d+[.)]\s*")


def _criteria_items(criteria):
    """완료 기준을 줄(항목)로 나눈다. 목록 표시가 있으면, 표시 없는 줄은 앞 항목이 이어진 것으로 본다"""
    lines = [l.strip() for l in criteria.splitlines() if l.strip()]
    if not any(BULLET_RE.match(l) for l in lines):
        return lines
    if (not BULLET_RE.match(lines[0]) and not CHECK_MARK_RE.search(lines[0])
            and (re.search(r"[:：]$", lines[0]) or re.search(r"아래|다음", lines[0]))):
        lines = lines[1:]                        # "아래 두 가지"처럼 목록을 소개하는 말은 기준이 아니다
    items = []
    for l in lines:
        if BULLET_RE.match(l) or not items:
            items.append(BULLET_RE.sub("", l).strip())
        else:
            items[-1] += " " + l
    return [i for i in items if i]


def _split_check(item):
    """(확인 앞부분, 확인 방법 또는 None). 첫 '확인:'에서 나눈다"""
    m = CHECK_MARK_RE.search(item)
    if not m:
        return item, None
    body = re.sub(r"[\s—\-→]+$", "", item[:m.start()]).strip()
    return body, item[m.end():].strip()


def check_criteria(criteria):
    """(오류, 경고, 확인 앞부분 목록)"""
    errors, warnings, bodies = [], [], []
    items = _criteria_items(criteria)
    pass_only = bool(items)
    for item in items:
        left = [x for x in CRITERIA_PLACEHOLDERS if x in item]
        if left or _is_placeholder(item):
            errors.append(f"완료 기준에 템플릿 자리표시자 '{left[0] if left else item}'가 남아 있습니다. 실제 내용으로 바꾸세요")
            pass_only = False
            continue
        body, how = _split_check(item)
        bodies.append(body)
        is_pass = bool(PASS_RE.search(body))
        pass_only = pass_only and is_pass
        if how is None and not is_pass:
            errors.append(f"완료 기준 '{item}'에 확인 방법이 없습니다. 줄 끝에 '— 확인: …'을 붙여 무엇으로 확인할지 "
                          f"쓰세요(기존 테스트 / 테스트 추가 / 사람이 직접)")
        elif how is not None and not how:
            errors.append(f"완료 기준 '{item}'의 '확인:' 뒤가 비었습니다. 무엇으로 확인할지 쓰세요"
                          f"(기존 테스트 / 테스트 추가 / 사람이 직접)")
        elif how and re.match(r"사람이\s*직접", how.strip()) and DEV_TOOL_RE.search(how):
            warnings.append(f"완료 기준 '{body}'의 확인 방법에 개발 도구({DEV_TOOL_RE.search(how).group(0)})가 있습니다. "
                            f"'사람이 직접'은 사용자가 화면에서 할 수 있는 일로 쓰세요")
        plain = _strip_quotes(body)
        strong = ph._matches(UNOBSERVABLE_STRONG, QUANTITY_ABOVE_RE.sub(" ", plain))
        weak = ph._matches(UNOBSERVABLE_WEAK, plain)
        if strong and is_pass:                   # "통과한다"류 줄은 ✗로 고치게 하지 않는다
            warnings.append(f"완료 기준 '{body}'의 '{', '.join(strong)}'는 확인할 수 없는 표현입니다. 통과하는 명령 이름을 쓰세요")
        elif strong:
            errors.append(f"완료 기준 '{body}'은(는) 확인할 수 없는 표현({', '.join(strong)})입니다. "
                          f"눈으로 볼 결과(보이는 문구, 통과하는 명령, 돌려받는 값)로 바꾸세요")
        elif weak:
            warnings.append(f"완료 기준 '{body}'의 '{', '.join(weak)}'는 기준이 흐립니다. 가능하면 관찰할 결과로 바꾸세요")
    if pass_only:
        warnings.append("완료 기준이 검증 명령 통과뿐입니다. 다 된 뒤 사용자가 보게 될 결과를 한 줄 이상 쓰세요")
    return errors, warnings, bodies


def _norm_space(text):
    return " ".join(text.split())


def check_rules(rules):
    """작업 규칙 칸에 SKILL.md 형식의 여섯 문장이 모두 그대로 있는지. 줄을 더한 것은 괜찮다"""
    have = {_norm_space(BULLET_RE.sub("", l)) for l in rules.splitlines() if l.strip()}
    missing = [r for r in ph.WORK_RULES if _norm_space(r) not in have]
    if not missing:
        return []
    return [f"작업 규칙이 형식과 다릅니다(빠지거나 바뀐 줄 {len(missing)}개, 예: '{missing[0][:25]}…'). "
            f"SKILL.md 형식의 여섯 줄을 그대로 쓰세요"]


def _code_marks(text):
    """상황 칸에서 찾은 코드 표기 (따옴표 안 화면 문구는 뺀다)"""
    found = ["`"] if "`" in text else []
    plain = _strip_quotes(text)
    for pat in SITUATION_CODE_PATTERNS:
        for m in re.finditer(pat, plain):
            word = m.group(0)
            if word.lower() in ph.NOT_PATHS or word in found:
                continue
            found.append(word)
    return found[:3]


def _from_request(item, request):
    """'요청 — …' 제약이 요청 원문에 있는 말인지. '요청'으로 시작하지 않는 항목은 True"""
    m = re.match(r"\s*요청\s*[—\-:：]\s*", item)
    if not m:
        return True
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", item[m.end():])
    if not words:
        return True
    low = request.lower()
    hits = sum(1 for w in words if w[:2].lower() in low)
    return hits / len(words) >= REQUEST_WORD_MIN_RATIO


def _count_assumptions(value):
    """가정 항목 수: 불릿 줄 수(불릿이 없으면 줄 수, '없음'이면 0)"""
    if not value.strip() or NONE_MARK.match(value):
        return 0
    lines = [l for l in value.splitlines() if l.strip()]
    bullets = [l for l in lines if BULLET_RE.match(l)]
    return len(bullets) if bullets else len(lines)


def _git_pathspec(root):
    """스킬 자신의 폴더는 찾는 범위에서 뺀다 (그 안의 설명 글자가 주소로 걸리지 않게)"""
    spec = ["--", "."]
    try:
        rel = Path(__file__).resolve().parent.relative_to(Path(root).resolve())
    except ValueError:
        return spec
    if rel != Path("."):
        spec.append(f":(exclude){rel.as_posix()}")
    return spec


def _git_out(args, root):
    """git 출력 줄. git이 없거나 저장소가 아니거나 시간이 넘으면 None, 못 찾았으면 빈 목록"""
    try:
        p = subprocess.run(["git", "-C", str(root)] + args, capture_output=True, timeout=GIT_GREP_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode not in (0, 1):
        return None
    return p.stdout.decode("utf-8", "replace").splitlines()


VALUE_SLOT_END_RE = re.compile(r"/(:[A-Za-z0-9_]+|\{[^}]*\}|\$\{[^}]*\})$")


def _line_has_api(line, api, tail=False):
    """줄에 주소가 '주소로' 적혀 있는지: 뒤에 이어지는 이름이 없고, 폴더 경로의 일부가 아니어야 한다"""
    i = line.find(api)
    while i != -1:
        end = i + len(api)
        if not (end < len(line) and re.match(r"[A-Za-z0-9_\-]", line[end])):
            if i == 0 or not re.match(r"[A-Za-z0-9_.\-]", line[i - 1]):
                return True
            if tail and VALUE_SLOT_END_RE.search(line[:i]):    # '/:id/cancel'의 /cancel
                return True
            if "://" in line[:i].split()[-1]:
                return True
        i = line.find(api, i + 1)
    return False


def _api_in_paths(api, files):
    """파일 경로로 주소를 만드는 저장소(app/api/orders/route.ts, pages/api/orders.ts)인지"""
    segs = [x for x in api.split("/") if x]
    n = len(segs)
    for f in files:
        parts = f.split("/")
        if f.startswith(("app/", "src/app/")) and parts[-1].split(".")[0] not in ("route", "page"):
            continue                            # Android의 app/ 모듈 같은 폴더와 겹치지 않게
        if segs[0] != "api" and not f.startswith(ROUTE_DIRS):
            continue
        for j in range(len(parts) - n + 1):
            w = parts[j:j + n]
            if w[:-1] == segs[:-1] and segs[-1] in (w[-1], w[-1].rsplit(".", 1)[0]):
                return True
    return False


def _in_repo(api, root, tail=False):
    """주소가 저장소 추적 파일에 주소로 있으면 True, 없으면 False, git으로 알 수 없으면 None"""
    spec = _git_pathspec(root)
    lines = _git_out(["grep", "-F", "-h", "-I", "--no-color", "-e", api] + spec, root)
    if lines is None:
        return None
    if any(_line_has_api(l, api, tail) for l in lines):
        return True
    files = _git_out(["ls-files"] + spec, root)
    if files is None:
        return None
    return _api_in_paths(api, files)


def _api_groups(path):
    """주소를 값 자리({id}, :id)로 나눈 고정 구간들. '/orders/{id}/cancel' → ['/orders', '/cancel'].
    값 자리로 시작하는 주소는 점검하지 않는다(빈 목록)"""
    groups, cur = [], []
    for seg in [x for x in path.split("/") if x]:
        m = re.search(r"[{:]", seg)
        if not m:
            cur.append(seg)
            continue
        if m.start() and not groups:
            cur.append(seg[:m.start()])
        if cur:
            groups.append("/" + "/".join(cur))
        elif not groups:
            return []
        cur = []
    if cur:
        groups.append("/" + "/".join(cur))
    return groups


def _unknown_apis(text, root, request=""):
    """저장소에서 찾을 수 없는 'POST /orders' 모양의 주소. git이 없거나 저장소가 아니면 빈 목록.
    요청 원문에 있는 주소는 사용자가 말한 것이라 점검하지 않는다"""
    out = []
    for m in API_RE.finditer(text):
        path = m.group(2).rstrip(".:-")                                  # 문장 끝의 마침표 등은 뗀다
        label = f"{m.group(1)} {path}"
        if label in out or path in request:
            continue
        for n, api in enumerate(_api_groups(path)):    # 값이 들어가는 칸은 떼고, 앞뒤 고정 구간이 각각 주소로 있어야 한다
            found = _in_repo(api, root, tail=n > 0)
            if found is None:
                return []                      # 이 점검은 건너뛴다
            if not found:
                out.append(label)
                break
    return out


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
    bodies = []
    if criteria and not _is_placeholder(criteria):
        e, w, bodies = check_criteria(criteria)
        errors += e
        warnings += w
        if TEST_ADD_RE.search(criteria) and target and not _has_test_path(target):
            warnings.append("완료 기준의 확인에 '테스트를 추가'가 있는데 대상에 테스트 파일이 없습니다. "
                            "테스트를 넣을 파일 경로를 대상에 적으세요(없는 파일이면 뒤에 '(새 파일)')")

    vague = ph._matches(ph.VAGUE_PATTERNS, _strip_quotes(goal + "\n" + "\n".join(bodies)))
    if vague:
        warnings.append(f"모호한 표현이 남아 있습니다: {', '.join(vague)}")

    rules = f.get("작업 규칙", "")
    if rules.strip():
        errors += check_rules(rules)

    n_assume = _count_assumptions(f.get("가정", ""))
    if n_assume > MAX_ASSUMPTIONS:
        warnings.append(f"가정이 {n_assume}개입니다. 일하는 방법(고칠 파일, 테스트 추가)은 가정에서 빼세요. 그래도 사용자가 "
                        f"원하는 결과를 대신 정한 것이 넷 이상이면 프롬프트를 보여 주기 전에 가장 중요한 것 하나를 물으세요")

    for api in _unknown_apis("\n".join(f.get(k, "") for k in ("목표", "완료 기준", "가정", "상황")), root,
                             f.get("요청 원문", "")):
        warnings.append(f"'{api}'은(는) 저장소에서 찾을 수 없는 주소입니다. 직접 찾아보고, 없으면 지어낸 주소이니 프롬프트에서 빼세요. "
                        f"사용자에게는 주소가 아니라 화면에서 무엇이 보이면 다 된 것인지를 묻습니다")

    situation = f.get("상황", "")
    if situation and not _is_placeholder(situation):
        code = _code_marks(situation)
        if code:
            warnings.append(f"상황에 코드 표기({', '.join(code)})가 있습니다. 상황은 사용자가 쓰는 말로 쓰고 "
                            f"파일·함수 이름은 목표·대상·참고에 쓰세요")
        terms = ph._matches(TECH_TERMS, _strip_quotes(situation))
        if terms:
            warnings.append(f"상황에 개발 용어({', '.join(terms)})가 있습니다. 화면에서 보이는 일로 바꾸세요")

    in_request = {m.group(0).lower() for m in SERVER_TERM_RE.finditer(f.get("요청 원문", ""))}
    seen_terms, seen_fields = [], []
    for name, value in (("상황", f.get("상황", "")), ("목표", goal), ("완료 기준", "\n".join(bodies)),
                        ("가정", f.get("가정", ""))):
        for m in SERVER_TERM_RE.finditer(_strip_quotes(value)):
            term = m.group(0)
            if name == "상황" and term.lower() == "api":
                continue                         # 상황의 API는 위의 개발 용어 △가 이미 낸다
            if term.lower() in in_request:
                continue
            if term.upper() == "API":
                term = "API"
            if term not in seen_terms:
                seen_terms.append(term)
            if name not in seen_fields:
                seen_fields.append(name)
    if seen_terms:
        warnings.append(f"요청 원문에 없는 {', '.join(repr(t) for t in seen_terms)}이(가) {'·'.join(seen_fields)}에 있습니다. "
                        f"저장소에 이미 있는 것을 가리키면 그대로 두고, 사용자가 말하지 않은 서버 연결을 새로 만들기로 정한 것이면 "
                        f"빼고 화면에서 보이는 결과만 쓰세요")

    constraint = f.get("제약", "")
    if constraint and not _is_placeholder(constraint) and not NONE_MARK.match(constraint):
        for item in _items(constraint):
            left = [x for x in CONSTRAINT_PLACEHOLDERS if x in item]
            if left:
                errors += [f"제약에 자리표시자 '{x}'가 남아 있습니다. 실제 문장으로 바꾸고, 쓸 것이 없으면 그 항목을 지우세요"
                           for x in left]
                continue
            if not NONE_MARK.match(item) and not SOURCE_MARK.search(item):
                warnings.append(f"제약 '{item}'에 출처가 없습니다. 출처(예: AGENTS.md —)를 붙이거나, 요청에 없던 것이면 가정으로 옮기세요")
                continue
            if f.get("요청 원문") and not _from_request(item, f["요청 원문"]):
                warnings.append(f"제약 '{item}'은(는) 요청 원문에서 찾을 수 없습니다. 사용자가 한 말이 아니면 가정으로 옮기세요")

    # 작업 규칙의 고정 문장("커밋·push…")은 위험 작업 요청이 아니므로 빼고 본다
    danger = ph._matches(ph.DANGER_PATTERNS, "\n".join(v for k, v in f.items() if k != "작업 규칙"))
    if danger:
        warnings.append(f"위험 작업 표현이 있습니다({', '.join(danger)}). 키워드 기준이므로, 그 작업을 실제로 "
                        f"실행하는 프롬프트일 때만 사용자에게 작업 이름을 들어 승인받으세요")

    return {"fields": f, "errors": errors, "warnings": warnings}


def check_question(text):
    """사용자에게 보낼 질문의 △ 목록: 질문이 많음, 코드 표기, 개발 용어, 보기 없음"""
    warnings = []
    n = len(re.findall(r"[?？][?？!！]*", text))
    if n > MAX_QUESTIONS:
        warnings.append(f"질문이 {n}개입니다. 한 번에 하나, 많아야 둘만 물으세요")
    code = _code_marks(text)
    if code:
        warnings.append(f"질문에 코드 표기({', '.join(code)})가 있습니다. 사용자는 파일·함수 이름을 모릅니다. "
                        f"화면에서 보이는 말로 바꾸세요")
    terms = ph._matches(QUESTION_TERMS, _strip_quotes(text))
    if terms:
        warnings.append(f"질문에 개발 용어({', '.join(terms)})가 있습니다. 사용자가 답할 수 있게 화면에서 보이는 말로 바꾸세요")
    has_choices = (re.search(r"\(\s*예\s*[:：]", text) or any(BULLET_RE.match(l) for l in text.splitlines())
                   or re.search(r"\S\s+/\s+\S", text))
    if not has_choices:
        warnings.append("보기가 없습니다. 저장소에서 찾은 화면·기능이나 예를 보기로 주세요")
    return warnings


def question_to_text(warnings):
    return "\n".join(["질문 점검"] + [f"  △ {w}" for w in warnings] + [QUESTION_LAST_WARN if warnings else QUESTION_LAST_OK])


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
                   "3번째에도 ✗가 남으면 다시 돌리지 말고, 무엇을 확인하지 못했는지를 답의 '확인하지 못한 것'에 평소 말로 쓰세요")
    else:
        out.append("→ 점검 끝. 사용자에게는 점검 이야기를 하지 말고 '이렇게 이해했습니다'로 시작하는 답을 바로 쓰세요")
    if any(w.startswith("검증") for r in result["prompts"] for w in r["warnings"]):
        out.append("→ '검증'으로 시작하는 △를 고치지 못했으면 무엇을 확인하지 못했는지를 답의 '확인하지 못한 것'에 평소 말로 쓰세요")
    return "\n".join(out)


def main(argv):
    args = list(argv[1:])
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    for stream in (sys.stdout, sys.stderr):    # 로캘이 UTF-8이 아니어도 한글 출력에서 죽지 않게
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    as_json = "--json" in args
    question = "--question" in args
    args = [a for a in args if a not in ("--json", "--question")]
    explicit_root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            sys.stderr.write("[prompt_lint] --root 뒤에 경로가 없습니다\n")
            return 2
        explicit_root = args[i + 1]
        del args[i:i + 2]

    text = " ".join(args) if args else ("" if sys.stdin.isatty() else ph.read_stdin())
    if question:                                   # 사용자에게 보낼 질문 점검 — 저장소는 보지 않는다
        if not text.strip():
            sys.stderr.write("[prompt_lint] 점검할 질문이 없습니다\n")
            return 2
        print(question_to_text(check_question(text)))
        return 0
    if not split_prompts(text):
        print("점검할 프롬프트가 없습니다 — '요청 원문: ...', '목표: ...'처럼 '칸 이름:' 형식으로 다시 쓰고 점검하세요")
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
