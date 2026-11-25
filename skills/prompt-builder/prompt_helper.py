#!/usr/bin/env python3
"""prompt-builder 스킬 보조 스크립트

대충 쓴 요청("이거 버그났어", "뭐가 안 보여")과 저장소를 살펴, 에이전트가 실행용 프롬프트를
만들 때 쓸 단서를 뽑는다. 산출물은 단서이고, 판단(물을지, 무엇으로 채울지)은 SKILL.md가 한다.
  - 요청에 빠진 항목(대상·완료 기준·검증·제약) — 키워드 기반이라 참고용
  - 가리키는 말("이거", "뭐가") — 무엇을 가리키는지 요청에 없는 말. 요청의 다른 말로 알 수 없으면 묻고, 저장소에서 찾은 문제가 그것이라고 짐작하지 않는다
  - 끝이 없는 표현("예쁘게", "느려") — 어디까지 하면 끝인지 없는 말. 범위와 끝낼 조건을 먼저 좁힌다
  - 새로 만드는 요청("붙여줘", "추가해줘") — 없던 것을 만들라는 말. 다 된 모습은 사용자만 알아서, 요청에 없으면 묻는다
  - 모호한 표현, 위험 작업, 작업 크기 — 키워드 기반이라 참고용 (빠뜨릴 수 있음)
  - 요청에 나온 경로가 저장소에 있는지 — 파일 시스템으로 확인
  - 저장소 설정 파일에서 찾은 검증 명령 후보 — 설정 파일 존재로 추정

표준 라이브러리만 사용한다 (Python 3.8 이상).

사용법 (요청은 stdin heredoc으로 넘긴다 — 따옴표·백틱이 섞여도 안전하도록)
  python3 prompt_helper.py scan [--root 저장소경로] [--text] <<'PROMPT_BUILDER_EOF'
  로그인 버그 고쳐 줘
  PROMPT_BUILDER_EOF

  python3 prompt_helper.py scan --text "로그인 버그 고쳐 줘"   # 짧은 요청은 인자로도 가능
  python3 prompt_helper.py template

종료 코드: 0 정상, 2 입력 오류(빈 요청, 없는 루트 등)
"""

import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 조정 가능한 설정 — 팀 상황에 맞게 이 부분만 고치면 된다
# ─────────────────────────────────────────────────────────────

# 영문 키워드 경계. 파이썬 \b는 한글도 글자로 보기 때문에 "push해줘"에서 경계를 못 찾는다.
# 그래서 영문·숫자·밑줄만 경계 판단에 쓴다.
L = r"(?<![A-Za-z0-9_])"
R = r"(?![A-Za-z0-9_])"

# 작업 지시로 보는 동사 어간 (활용형은 어간으로 잡는다: 고치/고쳐/고쳤)
TASK_VERBS = [
    r"만들", r"만든", r"추가", r"수정", r"고치", r"고쳐", r"고쳤", r"구현", r"작성",
    r"변경", r"바꾸", r"바꿔", r"바꿨", r"개선", r"리팩터", r"리팩토링", r"삭제", r"제거",
    r"옮기", r"옮겨", r"이전", r"마이그레이션", r"적용", r"연동", r"붙여", r"넣어", r"막아",
    r"검토", r"리뷰", r"분석", r"조사", r"정리", r"해결", r"처리", r"생성", r"올려", r"배포",
    r"봐\s*줘", r"봐\s*주", r"띄우", r"띄워", r"반영",
    L + r"(fix|add|implement|update|refactor|write|create|remove|migrate|review)",
]

FIELDS = {
    "대상": {
        "설명": "어떤 파일·폴더·모듈·화면을 다루는지",
        "패턴": [
            r"[A-Za-z0-9_\-./]+\.(py|ts|tsx|js|jsx|kt|kts|java|dart|swift|go|rs|md|json|ya?ml|sql|gradle|xml|html|css)" + R,
            r"[A-Za-z0-9_\-]+/[A-Za-z0-9_\-./]+", r"`[^`]+`",
            r"대상\s*[:：]", r"범위\s*[:：]", r"파일", r"폴더", r"디렉터리", r"모듈",
            r"클래스", r"함수", r"메서드", r"화면", r"페이지", r"컴포넌트", r"엔드포인트", L + r"API" + R,
            r"\d+\s*장", r"\d+\.\d+\s*절",
        ],
    },
    "완료 기준": {
        "설명": "무엇이 되면 끝인지 (눈으로 확인할 수 있는 결과)",
        "패턴": [
            r"완료\s*기준", r"완료\s*조건", r"성공\s*기준", r"기대\s*결과", r"결과물", r"산출물",
            r"되면\s*(완료|끝)", r"하면\s*(완료|끝)", r"끝나면", r"통과하면", r"돼야", r"되어야",
            r"보여야", r"나와야", r"acceptance", r"done\s*when",
        ],
    },
    "검증": {
        "설명": "끝났는지 무엇으로 확인하는지 (명령어·테스트)",
        "패턴": [
            r"테스트(?!\s*(화면|페이지|계정|데이터|서버|용))", L + r"test" + R, r"빌드", L + r"build" + R,
            r"analyze", r"lint", r"pytest", r"npm\s+run", r"gradlew",
            r"(flutter|dart)\s+(analyze|test|run|format|fix|build)" + R, r"검증", r"확인\s*방법",
            r"실행해\s*보", r"돌려\s*보", L + r"CI" + R,
        ],
    },
    "제약": {
        "설명": "건드리면 안 되는 것, 지켜야 할 규칙",
        "패턴": [
            r"제약", r"하지\s*마", r"하지\s*말", r"금지", r"건드리지", r"바꾸지", r"유지(?!보수)",
            r"만\s*수정", r"제외", r"말고", r"없이", L + r"don'?t" + R,
        ],
    },
}

VAGUE_PATTERNS = [
    r"알아서", r"적당히", r"대충", r"잘\s*좀", r"좋게", r"깔끔하게", r"등등", r"이것저것",
    r"전부\s*다", r"다\s*해\s*줘", r"웬만하면", r"느낌", r"그거", r"저번(에|처럼)", r"지난번",
]

# 가리키는 말 — 무엇을 가리키는지 요청에 없는 말 (요청의 다른 말로 알 수 없으면 묻고, 저장소에서 찾은 문제가 그것이라고 짐작하지 않는다)
POINTER_PATTERNS = ["(?<![가-힣])" + p for p in [     # 앞 글자가 한글이면 낱말 안이다 ("빈 칸이거나")
    r"이거", r"이것", r"그거", r"그것", r"저거", r"저것", r"여기", r"거기", r"저기",
    r"뭐가", r"뭔가", r"아까", r"저번(에|처럼)", r"지난번",
]]

# 끝이 없는 표현 — 어디까지 하면 끝인지 없는 말 (범위와 끝낼 조건을 먼저 좁힌다)
OPEN_ENDED_PATTERNS = [
    r"예쁘게", r"이쁘게", r"깔끔하게", r"보기\s*좋게", r"좋게", r"편하게", r"빠르게",
    r"느려", r"느린", r"느림", r"느리", r"개선", r"최적화", r"정리", r"다듬", r"손봐",
    r"리팩터", r"리팩토링", r"알아서", r"적당히", r"잘\s*좀",
]

# 새로 만드는 요청 — 없던 것을 만들라는 말. 다 된 모습은 사용자만 안다 (요청에 없으면 묻는다)
_ASK = r"\s*(줘|주세요|주라|주면|줬으면|달라|줄래|주실|주시)"
# "해" 뒤에 도·서·놓·봤 등이 오면 요청이 아니다 ("연결해도", "구현해 놓은", "추가해서", "추가해 봤는데")
NEW_FEATURE_PATTERNS = [
    r"(추가|구현|연동|연결|도입|생성)\s*(해(?!\s*(도|서|놓|놨|봤|본|보니|볼|둔|뒀|주신|야\s*하는데))|좀|부탁|바람|하고\s*싶|필요(?!\s*없)|$)",
    r"(만들어|붙여|넣어|달아)(?=\s*($|[.!?~]))",
    r"(만들어|붙여|넣어|달아|짜)" + _ASK,
]

# 위험 작업 — 되돌리기 어렵거나 저장소 밖에 영향을 주는 것.
# SKILL.md 4단계의 위험 작업 목록과 맞춰 둔다 (테스트로 확인).
DANGER_PATTERNS = [
    L + r"push" + R, r"푸시", L + r"force" + R, r"강제", r"reset\s+--hard", r"rm\s+-rf",
    L + r"drop" + R, L + r"truncate" + R, r"삭제", L + r"delete" + R,
    r"배포", L + r"deploy", L + r"prod(uction)?" + R, r"프로덕션", r"운영\s*(서버|DB|환경|에|으로|반영)",
    r"머지", L + r"merge" + R, r"커밋", L + r"commit" + R, L + r"PR" + R, r"풀\s*리퀘스트",
    r"릴리스", r"릴리즈", L + r"release" + R, L + r"publish" + R, r"마이그레이션", L + r"migrat",
]

# 한 프롬프트에 담기 많은 작업 기준 — 넘으면 프롬프트를 나누라고 알린다
MAX_NUMBERED_ITEMS = 5
MAX_LENGTH = 4000

# 경로를 찾을 때 건너뛸 폴더 (점 폴더라도 .github 등은 찾는다)
SKIP_DIRS = {".git", "node_modules", "build", "dist", ".dart_tool", ".gradle", ".idea",
             "__pycache__", ".venv", "venv", "Pods", ".next", "target", "out", ".cache"}
MAX_PATH_MATCHES = 5
MAX_PATH_LEN = 200

# npm init이 만드는 기본 test 스크립트 — 실제 테스트가 아니므로 제외
NPM_DEFAULT_TEST = "no test specified"

# 설정 파일 → 검증 명령 탐지에 쓰는 파일 이름 (모노레포 하위 폴더 탐색용)
PROJECT_MARKERS = ("package.json", "pubspec.yaml", "build.gradle", "build.gradle.kts",
                   "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml", "Makefile")

# ─────────────────────────────────────────────────────────────
# 요청 분석
# ─────────────────────────────────────────────────────────────

FLAGS = re.IGNORECASE | re.MULTILINE


def _any(patterns, text):
    return any(re.search(p, text, FLAGS) for p in patterns)


def _matches(patterns, text):
    found = []
    for p in patterns:
        m = re.search(p, text, FLAGS)
        if m and m.group(0).strip() not in found:
            found.append(m.group(0).strip())
    return found


def analyze_request(text):
    """요청 문장만 보고 알 수 있는 것 (키워드 기반 — 참고용)"""
    raw_length = len((text or "").strip())
    text = drop_long_words(text).strip()
    present = [n for n in FIELDS if _any(FIELDS[n]["패턴"], text)]
    missing = [n for n in FIELDS if n not in present]
    numbered = len(re.findall(r"^\s*(\d+[.)]|[-*•])\s+", text, re.MULTILINE))
    return {
        "is_task": _any(TASK_VERBS, text),
        "present_fields": present,
        "missing_fields": missing,
        "pointers": _matches(POINTER_PATTERNS, text),
        "open_ended": _matches(OPEN_ENDED_PATTERNS, text),
        "new_feature": _matches(NEW_FEATURE_PATTERNS, text),
        "vague": _matches(VAGUE_PATTERNS, text),
        "danger": _matches(DANGER_PATTERNS, text),
        "too_big": numbered > MAX_NUMBERED_ITEMS or raw_length > MAX_LENGTH,
        "numbered_items": numbered,
        "length": raw_length,
    }


# ─────────────────────────────────────────────────────────────
# 저장소 분석
# ─────────────────────────────────────────────────────────────

URL_RE = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://\S+")
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9_.+\-])[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+")
# 경로 한 구간: 영문·숫자·기호, Next.js의 [id]·[...slug], @modal, $id. (shop)처럼 구간 전체가 괄호인 것(라우트 그룹)은 따로 받는다
_SEG = r"(?:[A-Za-z0-9_.\-@$]|\[[^\]\s/]*\])+"
_GROUP_SEG = r"\([^)\s/]+\)"
# 경로 후보: 백틱 내용, 확장자 있는 파일 이름, 슬래시가 든 경로 (영문·숫자·기호만 — 조사 앞에서 끊긴다)
PATH_RE = re.compile(
    r"`([^`\n]+)`"
    r"|(?<![A-Za-z0-9_\-./가-힣])([A-Za-z0-9_\-./]*[A-Za-z0-9_\-]\.[A-Za-z][A-Za-z0-9]{0,4})(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_\-./])(" + _SEG + r"/(?:" + _SEG + "|" + _GROUP_SEG + r")(?:/(?:" + _SEG + "|" + _GROUP_SEG + r")?)*)"
)
# 파일 이름처럼 보이지만 파일이 아닌 흔한 표기
NOT_PATHS = {"e.g", "i.e", "etc", "vs", "node.js", "next.js", "vue.js", "nuxt.js", "react.js"}


def _read(path):
    """설정 파일 읽기 — BOM·비UTF-8이 섞여도 죽지 않는다"""
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


_INDEX = {}


def _index(root):
    """저장소의 이름 → 경로 색인. os.walk는 저장소마다 한 번만 돈다"""
    key = str(root)
    if key not in _INDEX:
        exact, lower = {}, {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for entry in sorted(filenames) + dirnames:
                rel = str(Path(dirpath, entry).relative_to(root))
                nfc = unicodedata.normalize("NFC", entry)       # macOS는 한글 이름을 풀어서(NFD) 저장한다
                exact.setdefault(nfc, []).append(rel)
                lower.setdefault(nfc.lower(), []).append(rel)
        _INDEX[key] = (exact, lower)
    return _INDEX[key]


def _walk_find(root, name, ignore_case=False):
    """파일·폴더 이름만 주어졌을 때 저장소에서 찾는다"""
    exact, lower = _index(root)
    name = unicodedata.normalize("NFC", name)
    hits = (lower.get(name.lower()) if ignore_case else exact.get(name)) or []
    return hits[:MAX_PATH_MATCHES]


def _inside(root, p):
    try:
        p.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


MAX_WORD_LEN = 200
_LONG_WORD_RE = re.compile(r"\S{%d,}" % (MAX_WORD_LEN + 1))


def drop_long_words(text):
    """공백 없이 200자를 넘는 덩어리는 경로·URL·이메일이 아니다. 정규식이 길게 되짚지 않게 미리 뺀다"""
    return _LONG_WORD_RE.sub(" ", text or "")


def read_stdin():
    """표준 입력을 UTF-8로 읽는다. 잘못된 바이트는 �로 바꿔 죽지 않게 한다"""
    buf = getattr(sys.stdin, "buffer", None)
    if buf is None:
        return sys.stdin.read()
    return buf.read().decode("utf-8", "replace")


def _candidates(text, root=None):
    text = URL_RE.sub(" ", drop_long_words(text))
    text = EMAIL_RE.sub(" ", text)
    seen = []
    for m in PATH_RE.finditer(text):
        tick, fname, slashed = m.group(1), m.group(2), m.group(3)
        if tick is not None:
            cand = tick.strip()
            if re.search(r"\s", cand):      # 공백이 든 백틱은 명령·문장이지 경로가 아니다
                continue
        else:
            cand = (fname or slashed or "").strip().rstrip(".,)")
        if (not cand or cand in seen or len(cand) > MAX_PATH_LEN
                or re.fullmatch(r"[\d.]+", cand) or cand.lower() in NOT_PATHS):
            continue
        if cand.startswith("$"):                # $HOME/notes 같은 환경 변수
            continue
        if re.fullmatch(r"@[\w.\-]+/[\w.\-]+", cand):    # @types/node 같은 패키지 이름은 저장소에 있을 때만 경로
            try:
                if not (root and (root / cand).exists()):
                    continue
            except OSError:
                continue
        seen.append(cand)
    return seen


# 한글이 섞인 경로 후보 ("문서/가이드/설치.md에서"). 조사가 붙어 있으므로 존재하는 것만 쓴다
HANGUL_PATH_RE = re.compile(r"(?<![\w\-./])([\w\-./]*[가-힣][\w\-./]*)")
# 확장자 없이도 파일로 보는 이름
BARE_FILENAMES = {"Dockerfile", "Makefile", "Jenkinsfile", "Procfile", "Gemfile", "Podfile",
                  "Brewfile", "Vagrantfile", "Rakefile", "LICENSE", "CODEOWNERS", "gradlew"}


# 경로로 인정하는 파일 확장자 (그 밖의 "a.b"는 코드 식별자로 본다: response.data, user.name)
FILE_EXTS = {
    "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "kt", "kts", "java", "dart", "swift", "go", "rs",
    "md", "mdx", "json", "yaml", "yml", "toml", "sql", "gradle", "xml", "html", "css", "scss", "vue",
    "svelte", "sh", "txt", "cfg", "ini", "lock", "properties", "plist", "c", "h", "cpp", "hpp", "m",
    "mm", "rb", "php", "cs", "env", "graphql", "proto", "csv", "png", "jpg", "svg",
}

MAX_PARTICLE_LEN = 4
EXT_TAIL_RE = re.compile(r"^(.*\.[A-Za-z][A-Za-z0-9]{0,4})[가-힣]{1,%d}$" % MAX_PARTICLE_LEN)


def hangul_candidates(text, root, include_missing=False):
    """한글이 섞인 경로 중 저장소에 실제로 있는 것. 끝의 조사는 한 글자씩(4자까지) 떼며 맞춰 본다.
    include_missing이면, 못 찾았어도 확장자로 끝나는 모양(조사 뗀 것)은 돌려준다"""
    out = []
    for m in HANGUL_PATH_RE.finditer(URL_RE.sub(" ", drop_long_words(text))):
        word = m.group(1).rstrip(".,)")
        if "/" not in word and not re.search(r"\.[A-Za-z][A-Za-z0-9]{0,4}", word):
            continue
        found_one = False
        cur, cut = word, 0
        while cur and cut <= MAX_PARTICLE_LEN:
            try:
                p = root / cur
                if ("/" in cur and p.exists() and _inside(root, p)) or \
                        ("/" not in cur and "." in cur and _walk_find(root, cur)):
                    if cur not in out:
                        out.append(cur)
                    found_one = True
                    break
            except OSError:
                break
            if not re.search(r"[가-힣]$", cur):
                break
            cur, cut = cur[:-1], cut + 1
        if include_missing and not found_one and re.search(r"[가-힣]", word):
            m2 = EXT_TAIL_RE.match(word)
            miss = m2.group(1) if m2 else (word if re.search(r"\.[A-Za-z][A-Za-z0-9]{0,4}$", word) else None)
            # 없는 것은 파일 확장자로 끝날 때만 경로로 본다 (response.data를 → 코드 식별자)
            if miss and miss.rsplit(".", 1)[-1].lower() in FILE_EXTS and miss not in out:
                out.append(miss)
    return out


_BARE_LOWER = {n.lower(): n for n in BARE_FILENAMES if n != "gradlew"}


def bare_file_candidates(text, root):
    """Dockerfile·Makefile처럼 확장자 없는 파일 이름 (대소문자 무시, 중복 제거)"""
    out = []
    seen = set()
    # 더 긴 이름의 일부(Dockerfile.dev, docker/Dockerfile)는 따로 뽑지 않는다. 뒤의 조사·마침표는 괜찮다
    for w in re.findall(r"(?<![A-Za-z0-9_./\-])[A-Za-z][A-Za-z0-9_\-]*(?![A-Za-z0-9_\-]|\.[A-Za-z0-9])", drop_long_words(text)):
        if w.lower() in _BARE_LOWER and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def existing_candidates(text, root):
    """모양으로는 경로인지 알 수 없지만 낱말 그대로 저장소에 있는 것 (.gitignore, build.gradle, go.mod, src/).
    점이나 슬래시가 든 낱말만 보고, 끝에 붙은 조사는 뗀다. 한글이 남는 낱말은 hangul_candidates가 맡는다.
    점과 슬래시만으로 된 낱말(./, ..., ../)은 경로 이름이 아니므로 뺀다"""
    out = []
    text = EMAIL_RE.sub(" ", URL_RE.sub(" ", drop_long_words(text)))
    for word in re.split(r"[\s,`'\"()\[\]<>“”‘’「」]+", text):
        word = re.sub(r"[가-힣]+$", "", word).rstrip(".,:;")
        if (len(word) < 2 or len(word) > MAX_PATH_LEN or word in out or not re.search(r"[./]", word)
                or re.fullmatch(r"[./]+", word) or re.search(r"[가-힣]", word) or word.startswith("/") or ".." in word
                or re.fullmatch(r"[\d.]+", word) or word.lower() in NOT_PATHS):
            continue
        try:
            if (root / word).exists() and _inside(root, root / word):
                out.append(word)
        except OSError:
            continue
    return out


def check_paths(text, root):
    """요청에 나온 경로·파일 이름이 저장소 안에 실제로 있는지"""
    results = []
    cands = _candidates(text, root)
    for c in (hangul_candidates(text, root, include_missing=True) + bare_file_candidates(text, root)
              + existing_candidates(text, root)):
        if c not in cands:                    # 출처가 달라도 같은 이름은 한 번만
            cands.append(c)
    cands = [c for c in cands if not any(o != c and o.startswith(c) and c.endswith("/") for o in cands)]
    for cand in cands:
        try:
            p = root / cand
            if p.exists() and _inside(root, p):
                results.append({"mention": cand, "exists": True, "found": [cand]})
                continue
            if cand.startswith("/") or ".." in Path(cand).parts:
                continue                      # 저장소 밖 경로는 다루지 않는다
            name = Path(cand).name
            found = _walk_find(root, name) or _walk_find(root, name, ignore_case=True)
            if "/" in cand:                   # 경로로 썼다면 끝부분이 맞는 것만
                tail = unicodedata.normalize("NFC", cand.strip("/")).lower()
                found = [f for f in found if unicodedata.normalize("NFC", f).lower().endswith(tail)] or found
            if (not found and "/" not in cand and cand.rsplit(".", 1)[-1].lower() not in FILE_EXTS
                    and cand.lower() not in _BARE_LOWER):
                continue                      # 없고 파일 확장자도 아니면 코드 식별자(response.data)로 본다
            results.append({"mention": cand, "exists": bool(found), "found": found})
        except OSError:
            continue
    return results


def _pkg_runner(d):
    """lock 파일로 러너를 정한다. 모노레포 하위 폴더는 lock 파일이 루트에 있으므로 위로 올라가며 찾는다"""
    cur = d.resolve()
    for _ in range(10):
        if (cur / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (cur / "yarn.lock").exists():
            return "yarn"
        if (cur / "bun.lockb").exists() or (cur / "bun.lock").exists():
            return "bun run"
        if (cur / "package-lock.json").exists() or (cur / ".git").exists() or cur.parent == cur:
            break
        cur = cur.parent
    return "npm run"


def _commands_in(d):
    """폴더 하나의 설정 파일에서 검증 명령 후보를 찾는다"""
    cmds = []
    pkg = d / "package.json"
    if pkg.exists():
        try:
            data = json.loads(_read(pkg) or "{}")
            scripts = data.get("scripts") if isinstance(data, dict) else None
            if isinstance(scripts, dict):
                runner = _pkg_runner(d)
                for key in ("typecheck", "type-check", "lint", "test", "build"):
                    val = scripts.get(key)
                    if isinstance(val, str) and NPM_DEFAULT_TEST not in val:
                        cmds.append(f"{runner} {key}")
        except ValueError:
            pass
    if (d / "gradlew").exists():
        cmds += ["./gradlew test", "./gradlew build"]
    if (d / "pom.xml").exists():
        cmds.append("mvn test")
    pub = d / "pubspec.yaml"
    if pub.exists():
        tool = "flutter" if re.search(r"sdk:\s*flutter", _read(pub)) else "dart"
        cmds.append(f"{tool} analyze")
        if (d / "test").is_dir():
            cmds.append(f"{tool} test")
    if any((d / f).exists() for f in ("pyproject.toml", "pytest.ini", "setup.cfg")):
        has_tests = any(d.glob("tests/**/test_*.py")) or any(d.glob("tests/**/*_test.py")) \
            or any(d.glob("test_*.py"))
        if has_tests or (d / "pytest.ini").exists():        # tests/에 __init__.py만 있으면 후보로 내지 않는다
            prefix = "uv run " if (d / "uv.lock").exists() else ""
            cmds.append(f"{prefix}pytest")
    if (d / "Cargo.toml").exists():
        cmds += ["cargo test", "cargo clippy"]
    if (d / "go.mod").exists():
        cmds += ["go test ./...", "go vet ./..."]
    mk = d / "Makefile"
    if mk.exists():
        targets = re.findall(r"^([\w\-]+):", _read(mk), re.MULTILINE)
        cmds += [f"make {t}" for t in ("lint", "test", "check") if t in targets]
    return cmds


def detect_verify_commands(root, targets=()):
    """루트와, 대상 경로에서 가장 가까운 하위 프로젝트의 검증 명령 후보.

    반환: [{"dir": ".", "cmd": "pnpm test"}, {"dir": "apps/web", "cmd": "pnpm test"}, ...]
    """
    out = []
    dirs = [root]
    for t in targets:                          # 대상에서 위로 올라가며 가장 가까운 프로젝트 폴더
        p = (root / t)
        cur = p if p.is_dir() else p.parent
        while _inside(root, cur) and cur != root:
            if any((cur / m).exists() for m in PROJECT_MARKERS):
                if cur not in dirs:
                    dirs.append(cur)
                break
            cur = cur.parent
    for d in dirs:
        try:
            rel = "." if d == root else str(d.relative_to(root))
            out += [{"dir": rel, "cmd": c} for c in _commands_in(d)]
        except Exception:                      # 한 폴더 오류가 전체를 죽이지 않게
            continue
    return out


def find_root(explicit=None):
    """저장소 루트: --root > git 최상위 > 현재 폴더"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=5)
        if top.returncode == 0 and top.stdout.strip():
            return Path(top.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.cwd().resolve()


def scan(text, root):
    root = Path(root).resolve()
    result = analyze_request(text)
    result["root"] = str(root)
    result["root_is_git"] = (root / ".git").exists()
    result["paths"] = check_paths(text, root)
    targets = [f for p in result["paths"] for f in p["found"]]
    result["verify_commands"] = detect_verify_commands(root, targets)
    return result


# ─────────────────────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────────────────────

# 작업 규칙 — 프롬프트 끝에 그대로 붙는 여섯 문장. 이 프롬프트만 받은 쪽도 같은 방식으로 일하게 한다.
# SKILL.md 4단계 형식과 글자 그대로 같아야 한다 (테스트로 확인)
WORK_RULES = (
    "고치기 전에 대상 파일과 관련 코드를 직접 읽고 원인이나 현재 구조를 확인한다. 읽지 않은 코드를 추측해서 고치지 않는다.",
    "완료 기준을 채우는 데 필요한 만큼만 바꾼다. 요청에 없는 정리·리팩터링·기능 추가는 하지 않는다.",
    "다 고친 뒤 완료 기준을 하나씩 '확인'에 적힌 방법으로 확인하고 검증 명령을 실제로 돌린다. 테스트를 통과시키려고 값을 끼워 맞추지 않는다.",
    "결과는 완료 기준별로 보고한다: 확인한 방법, 돌린 명령, 결과. 직접 확인하지 못한 기준은 못 했다고 쓰고 사용자가 무엇을 해 보면 되는지 적는다.",
    "대상 밖을 고쳐야 하거나 가정이 틀린 것 같으면 멈추고 묻는다.",
    "커밋·push·PR·배포·삭제는 사용자가 그 작업 이름을 들어 승인했을 때만 한다.",
)

# SKILL.md 4단계 코드 블록과 글자 그대로 같다 (테스트로 확인)
TEMPLATE = """요청 원문: (사용자가 쓴 말 그대로 — 되물어 받은 답은 / 로 이어 붙인다)
상황: (사용자가 쓰는 말로 — 지금 어디에서 무엇을 하면 어떻게 되는지, 어떻게 되면 되는지)
목표: (무엇을 하는지 한 문장)
대상: (실제로 존재하는 파일·모듈 경로)
완료 기준:
  - (사용자가 보게 될 결과) — 확인: (이 기준을 무엇으로 확인하는지)
  - (결과 2) — 확인: (확인 방법)
검증: (실제로 돌릴 명령)
제약: (지켜야 할 것 — 출처를 붙인다)
가정: (요청에 없어서 정한 것 — 사용자 확인 필요)
참고: (먼저 읽으면 좋은 코드·문서 위치)
작업 규칙:
""" + "\n".join("  - " + rule for rule in WORK_RULES)

# SKILL.md 1단계가 이 줄 이름들을 그대로 언급한다 (테스트로 확인)
LABELS = ("저장소 루트", "요청에 있는 항목", "요청에 빠진 항목", "가리키는 말", "끝이 없는 표현", "새로 만드는 요청", "모호한 표현",
          "위험 작업 표현", "작업이 큼", "경로", "검증 명령 후보")


def to_text(r):
    out = []
    root_note = "" if r["root_is_git"] else "  ⚠ git 저장소 루트가 아님 — 위치가 맞는지 확인"
    out.append(f"저장소 루트: {r['root']}{root_note}")
    out.append(f"요청에 있는 항목: {', '.join(r['present_fields']) or '없음'}  (키워드 기준, 확정 아님)")
    out.append(f"요청에 빠진 항목: {', '.join(r['missing_fields']) or '없음'}")
    if r["pointers"]:
        out.append(f"가리키는 말: {', '.join(r['pointers'])}  (무엇을 가리키는지 요청에 없음 — 요청의 다른 말로 알 수 없으면 묻는다. 저장소에서 찾은 문제가 그것이라고 짐작하지 않는다)")
    if r["open_ended"]:
        out.append(f"끝이 없는 표현: {', '.join(r['open_ended'])}  (어디까지 하면 끝인지 없음 — 범위와 끝낼 조건을 먼저 좁힌다)")
    if r["new_feature"]:
        out.append(f"새로 만드는 요청: {', '.join(r['new_feature'])}  (다 된 모습은 사용자만 안다 — 무엇이 보이면 끝인지 요청에 없으면 묻는다)")
    if r["vague"]:
        out.append(f"모호한 표현: {', '.join(r['vague'])}")
    if r["danger"]:
        out.append(f"위험 작업 표현: {', '.join(r['danger'])}  (키워드 기준 — 그 작업을 실제로 실행하라는 요청일 때만 위험 작업)")
    if r["too_big"]:
        why = f"목록 {r['numbered_items']}개" if r["numbered_items"] > MAX_NUMBERED_ITEMS else f"{r['length']}자"
        out.append(f"작업이 큼({why}) → 프롬프트를 나누는 것을 검토")
    for p in r["paths"]:
        if p["exists"]:
            extra = "" if p["found"] == [p["mention"]] else f" → {', '.join(p['found'])}"
            out.append(f"경로 '{p['mention']}': 있음{extra}")
        else:
            out.append(f"경로 '{p['mention']}': 저장소에서 못 찾음(경로가 아닐 수 있음)")
    cmds = [f"{c['cmd']}" if c["dir"] == "." else f"({c['dir']}에서) {c['cmd']}" for c in r["verify_commands"]]
    out.append("검증 명령 후보: " + (", ".join(cmds) or "찾지 못함") + "  (설정 파일 기준 — 실행 전 확인)")
    return "\n".join(out)


def _fail(msg):
    sys.stderr.write(f"[prompt_helper] {msg}\n")
    return 2


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    for stream in (sys.stdout, sys.stderr):    # 로캘이 UTF-8이 아니어도 한글 출력에서 죽지 않게
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    mode, args = argv[1], list(argv[2:])
    if mode == "template":
        print(TEMPLATE)
        return 0
    if mode != "scan":
        return _fail(f"알 수 없는 모드: {mode}")

    as_text = "--text" in args
    args = [a for a in args if a != "--text"]
    explicit_root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            return _fail("--root 뒤에 경로가 없습니다")
        explicit_root = args[i + 1]
        del args[i:i + 2]

    if args:                                   # 남은 인자는 요청 문장
        text = " ".join(args)
    elif sys.stdin.isatty():
        return _fail("요청이 비어 있습니다 — heredoc(<<'PROMPT_BUILDER_EOF')이나 인자로 넘기세요")
    else:
        text = read_stdin()
    if not text.strip():
        return _fail("요청이 비어 있습니다 — heredoc(<<'PROMPT_BUILDER_EOF')이나 인자로 넘기세요")

    root = find_root(explicit_root)
    if not root.is_dir():
        return _fail(f"저장소 루트가 폴더가 아닙니다: {root}")

    r = scan(text, root)
    print(to_text(r) if as_text else json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
