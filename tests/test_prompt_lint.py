"""prompt_lint 테스트 — 에이전트가 만든 프롬프트를 칸 단위로 점검하는지 확인한다.

원칙: ✗는 고칠 방법이 분명한 것만. 오탐 ✗는 모델이 멀쩡한 내용을 망가뜨리는 루프를 만든다.

실행: python3 -m unittest discover -s tests
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "prompt-builder"
sys.path.insert(0, str(SKILL_DIR))

import prompt_lint as pl  # noqa: E402

SCRIPT = str(SKILL_DIR / "prompt_lint.py")
SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

# SKILL.md 예시와 같은 저장소 모양
EXAMPLE_REPO = {
    "app/src/main/java/com/example/auth/LoginViewModel.kt": "",
    "app/src/test/java/com/example/auth/LoginViewModelTest.kt": "",
    "gradlew": "#!/bin/sh",
    "docs/setup.md": "## macOS 설치\n",
    "AGENTS.md": "문서는 한국어로 작성\n",
    # SKILL.md 예시 2·3 (메모 앱)
    "notes/search.py": "",
    "notes/templates/editor.html": "",
    "notes/static/editor.css": "",
    "tests/test_search.py": "",
    "tests/test_editor.py": "",
    "pyproject.toml": "",
}

# pnpm 모노레포 (Fable 검토에서 쓴 모양)
MONOREPO = {
    "package.json": json.dumps({"private": True, "scripts": {"lint": "eslint .", "test:unit": "vitest"}}),
    "pnpm-lock.yaml": "",
    "apps/web/package.json": json.dumps({"scripts": {"test": "vitest", "build": "vite build"}}),
    "apps/web/src/auth/login.ts": "",
    "apps/web/src/auth/session.ts": "",
    "apps/web/src/Login.test.tsx": "",
    ".github/workflows/ci.yml": "",
}

def rules_block(rules=None):
    """SKILL.md 형식의 작업 규칙 칸 (ph.WORK_RULES로 조립)"""
    return "작업 규칙:\n" + "".join(f"  - {r}\n" for r in (pl.ph.WORK_RULES if rules is None else rules))


RULES = rules_block()

GOOD = """요청 원문: 비밀번호 틀리면 앱이 꺼져
상황: 로그인 화면에서 비밀번호를 틀리게 넣으면 앱이 꺼진다. 틀려도 앱이 꺼지지 않고 다시 입력할 수 있어야 한다.
목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다
대상: app/src/main/java/com/example/auth/LoginViewModel.kt
완료 기준:
  - 잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다 — 확인: 기존 테스트 app/src/test/java/com/example/auth/LoginViewModelTest.kt
  - 검증 명령이 통과한다
검증: ./gradlew test
제약: (요청·저장소 문서에 없음)
가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)
참고: app/src/test/java/com/example/auth/LoginViewModelTest.kt
""" + RULES

WEB = """요청 원문: 로그인 실패 문구 바꿔 줘
상황: 로그인에 실패하면 보이는 문구를 바꾼다. 비밀번호가 틀리면 '비밀번호를 확인하세요'가 보여야 한다.
목표: 로그인 실패 메시지를 바꾼다
대상: apps/web/src/auth/login.ts
완료 기준:
  - 비밀번호가 틀리면 '비밀번호를 확인하세요'가 보인다 — 확인: 기존 테스트 apps/web/src/Login.test.tsx
검증: {verify}
제약: 없음
가정: 없음
""" + RULES


def make_repo(files=EXAMPLE_REPO):
    d = tempfile.TemporaryDirectory()
    root = Path(d.name).resolve()
    (root / ".git").mkdir()
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d, root


def result(prompt, root):
    return pl.lint(prompt, root)["prompts"][0]


def errors_of(prompt, root):
    return result(prompt, root)["errors"]


def warnings_of(prompt, root):
    return result(prompt, root)["warnings"]


class Basics(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def test_good_prompt_passes_clean(self):
        r = result(GOOD, self.root)
        self.assertEqual((r["errors"], r["warnings"]), ([], []))

    def test_code_fence_and_bold_labels(self):
        p = "```\n" + GOOD.replace("목표:", "**목표**:").replace("제약: (요청·저장소 문서에 없음)", "- **제약:** 없음") + "```\n"
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertFalse(any("출처" in w for w in r["warnings"]))     # '** 없음' 잔여 없음

    def test_missing_and_empty_fields(self):
        p = GOOD.replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)\n", "").replace(
            "제약: (요청·저장소 문서에 없음)", "제약:")
        errs = errors_of(p, self.root)
        self.assertTrue(any("'가정:' 칸이 없습니다" in e for e in errs))
        self.assertTrue(any("'제약:' 칸이 비었" in e and "없음" in e for e in errs))

    def test_template_placeholder_left(self):
        p = GOOD.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", "목표: (무엇을 하는지 한 문장)")
        self.assertTrue(any("자리표시자" in e for e in errors_of(p, self.root)))

    def test_label_variants_accepted_with_hint(self):
        p = (GOOD.replace("완료 기준:", "완료기준:").replace("검증:", "검증 방법:")
             .replace("제약:", "제약 사항:"))
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("표준 이름 '완료 기준:'" in w for w in r["warnings"]))

    def test_heading_labels(self):
        p = re.sub(r"^(요청 원문|상황|목표|대상|검증|제약|가정|참고): ", r"## \1\n", GOOD, flags=re.M).replace(
            "완료 기준:", "## 완료 기준").replace("작업 규칙:", "## 작업 규칙")
        self.assertEqual(errors_of(p, self.root), [])

    def test_unindented_bullets_do_not_overwrite_fields(self):
        p = GOOD.replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)",
                         "가정:\n- 검증: 수동 확인도 필요\n- 대상: src/auth/ghost.ts 도 볼 것")
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertIn("수동 확인도 필요", r["fields"]["가정"])

    def test_heading_goal_inside_body_does_not_split(self):
        p = GOOD.replace("  - 검증 명령이 통과한다", "  - 검증 명령이 통과한다\n## 목표\n위 목표 설명 보충")
        self.assertEqual(len(pl.lint(p, self.root)["prompts"]), 1)

    def test_prompt_headers_ignored(self):
        text = "프롬프트 1/2\n" + GOOD + "\n**프롬프트 2/2**\n" + GOOD.replace(
            "제약: (요청·저장소 문서에 없음)\n", "") .replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)",
            "가정: 없음\n제약: (요청·저장소 문서에 없음)")
        res = pl.lint(text, self.root)["prompts"]
        self.assertEqual(len(res), 2)
        self.assertFalse(any("프롬프트" in w for r in res for w in r["warnings"]))

    def test_titled_headers_and_short_first_prompt(self):
        first = "목표: a\n대상: app/src/main/java/com/example/auth/LoginViewModel.kt\n완료 기준:\n  - 검증 명령이 통과한다\n"
        text = "### 프롬프트 1/2: 모델\n" + first + "\n프롬프트 2/2 — 화면 수정\n" + GOOD
        res = pl.lint(text, self.root)["prompts"]
        self.assertEqual(len(res), 2)
        self.assertTrue(any("'검증:' 칸이 없습니다" in e for e in res[0]["errors"]))   # 짧은 1번도 따로 점검
        self.assertNotIn("프롬프트", res[0]["fields"].get("완료 기준", ""))

    def test_header_variants(self):
        for h in ["## [프롬프트 2/2] 화면", "프롬프트 2/2 (화면)", "프롬프트 2/2. 화면"]:
            with self.subTest(h=h):
                res = pl.lint(GOOD + "\n" + h + "\n" + GOOD, self.root)["prompts"]
                self.assertEqual(len(res), 2)
                self.assertNotIn("프롬프트", res[0]["fields"]["참고"])
                self.assertNotIn("프롬프트", res[0]["fields"]["작업 규칙"])

    def test_multiline_placeholder_in_criteria(self):
        for ph_line in ["(사용자가 보게 될 결과) — 확인: (이 기준을 무엇으로 확인하는지)",
                        "로그인 화면이 뜬다 — 확인: 기존 테스트 (테스트 파일 경로)",
                        "(결과 2) — 확인: pytest"]:
            with self.subTest(line=ph_line):
                p = GOOD.replace("  - 검증 명령이 통과한다", "  - " + ph_line)
                self.assertTrue(any("자리표시자" in e for e in errors_of(p, self.root)))

    def test_indented_bullets_in_assumption_are_not_fields(self):
        p = GOOD.replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)",
                         "가정:\n  - 대상: 다른 파일일 수도 있음\n  - 검증: 수동일 수도 있음")
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["fields"]["대상"], "app/src/main/java/com/example/auth/LoginViewModel.kt")


class Target(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo(MONOREPO)

    def tearDown(self):
        self.d.cleanup()

    def p(self, target):
        return WEB.format(verify="pnpm test").replace("대상: apps/web/src/auth/login.ts", f"대상: {target}")

    def test_missing_path_errors(self):
        self.assertTrue(any("저장소에 없습니다: apps/web/src/auth/nope.ts" in e
                            for e in errors_of(self.p("apps/web/src/auth/nope.ts"), self.root)))

    def test_new_file_marker(self):
        r = result(self.p("apps/web/src/auth/logout.ts (새 파일), apps/web/src/auth/session.ts"), self.root)
        self.assertEqual(r["errors"], [])
        r = result(self.p("apps/web/src/newdir/x.ts (새 파일)"), self.root)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("폴더가 아직 없습니다" in w for w in r["warnings"]))

    def test_marker_is_per_item(self):
        # 표시 하나가 다른 오타 경로까지 가리면 안 된다
        errs = errors_of(self.p("apps/web/src/auth/nothing.ts, apps/web/src/ghost/a.ts (확인 필요), "
                                "apps/web/src/auth/login.ts"), self.root)
        self.assertEqual(len(errs), 1)
        self.assertIn("nothing.ts", errs[0])

    def test_code_identifiers_ignored(self):
        t = "apps/web/src/auth/login.ts의 LoginService.login() 함수, package.json의 scripts.test"
        self.assertEqual(errors_of(self.p(t), self.root), [])

    def test_hangul_and_bare_filenames(self):
        (self.root / "문서/가이드").mkdir(parents=True)
        (self.root / "문서/가이드/설치.md").write_text("", encoding="utf-8")
        (self.root / "Dockerfile").write_text("", encoding="utf-8")
        for t in ["문서/가이드/설치.md", "Dockerfile", "문서/가이드/설치.md의 첫 절"]:
            with self.subTest(t=t):
                self.assertEqual(errors_of(self.p(t), self.root), [])
        self.assertTrue(errors_of(self.p("Makefile"), self.root))     # 없는 Makefile은 잡는다

    def test_new_file_marker_variants(self):
        for t in ["apps/web/src/auth/a.ts (신규 파일)", "apps/web/src/auth/a.ts — 새 파일", "apps/web/src/auth/b (새 폴더)"]:
            with self.subTest(t=t):
                self.assertEqual(errors_of(self.p(t), self.root), [])

    def test_outside_and_existing_new_file(self):
        self.assertTrue(any("저장소 밖" in e for e in errors_of(self.p("../other/file.ts"), self.root)))
        r = result(self.p("apps/web/src/auth/login.ts (새 파일)"), self.root)
        self.assertTrue(any("이미 있는 파일" in w for w in r["warnings"]))

    def test_root_file_with_paren(self):
        # 5차 2번: 'README.md (설치 절)', 'CHANGELOG.md (새 파일)'을 함수 호출로 보던 문제
        (self.root / "README.md").write_text("", encoding="utf-8")
        for t in ["CHANGELOG.md (새 파일)", "README.md (설치 절)", "docker-compose.yml (새 파일), .env.example (새 파일)"]:
            with self.subTest(t=t):
                self.assertEqual(errors_of(self.p(t), self.root), [])
        errs = errors_of(self.p("README.md, Nope.md(새 파일)"), self.root)
        self.assertEqual(errs, [])
        self.assertTrue(errors_of(self.p("README.md, Nope.md"), self.root))   # 표시 없는 없는 파일은 잡는다
        self.assertEqual(errors_of(self.p("apps/web/src/auth/login.ts의 login() 함수"), self.root), [])

    def test_missing_hangul_paths(self):
        # 5차 4번: 없는 한글 경로가 조용히 통과하던 문제
        self.assertTrue(errors_of(self.p("문서/가이드/없는문서.md"), self.root))
        self.assertTrue(errors_of(self.p("apps/web/src/없는문서.md"), self.root))
        (self.root / "문서/가이드").mkdir(parents=True)
        self.assertEqual(errors_of(self.p("문서/가이드/새문서.md (새 파일)"), self.root), [])

    def test_code_identifiers_with_particles(self):
        # 6차 새 문제 1: 조사 붙은 속성 접근 표기를 없는 파일로 보던 문제
        for t in ["apps/web/src/auth/login.ts의 response.data를 읽는 부분", "apps/web/src/auth/login.ts의 user.name을",
                  "apps/web/src/auth/login.ts의 this.state를", "apps/web/src/auth/login.ts의 입력값.trim()을",
                  "apps/web/src/auth/login.ts, utils.format() 호출부"]:
            with self.subTest(t=t):
                self.assertEqual(errors_of(self.p(t), self.root), [])

    def test_wrong_folder(self):
        # 6차 새 문제 3: 폴더가 틀린 경로가 조용히 통과하던 문제
        errs = errors_of(self.p("apps/mobile/src/auth/login.ts"), self.root)
        self.assertTrue(any("apps/web/src/auth/login.ts" in e for e in errs), errs)

    def test_existing_names_without_path_shape(self):
        # 점 파일·긴 확장자·최상위 폴더: 저장소에 있으면 helper와 같게 경로로 본다
        for rel in [".gitignore", ".env.example", "build.gradle", "gradle.properties", "go.mod", "Dockerfile.dev"]:
            (self.root / rel).write_text("", encoding="utf-8")
        (self.root / "tests").mkdir()
        for t in [".gitignore", ".env.example", "build.gradle", "gradle.properties", "go.mod", "Dockerfile.dev",
                  "tests", "tests/", "apps", "apps 폴더", "`.gitignore`"]:
            with self.subTest(t=t):
                self.assertEqual(errors_of(self.p(t), self.root), [])

    def test_single_name_new_or_dotfile(self):
        for t in [".env (새 파일)", "schema.graphql (새 파일)"]:
            with self.subTest(t=t):
                self.assertEqual(errors_of(self.p(t), self.root), [])
        self.assertTrue(any("저장소에 없습니다: .gitignore2" in e for e in errors_of(self.p(".gitignore2"), self.root)))
        self.assertTrue(any("경로가 없습니다" in e for e in errors_of(self.p("nope"), self.root)))

    def test_dot_words_inside_item_are_not_paths(self):
        for t in ["apps/web/src/auth/login.ts의 .then() 체인", "apps/web/src/auth/login.ts의 .header 규칙",
                  "apps/web/src/auth/login.ts, .then()"]:
            with self.subTest(t=t):
                self.assertEqual(errors_of(self.p(t), self.root), [])

    def test_helper_and_lint_agree(self):
        # helper가 '있음'이라고 한 경로를 lint가 대상으로 받아 ✗를 내면 안 된다
        for rel in [".gitignore", "build.gradle", "go.mod"]:
            (self.root / rel).write_text("", encoding="utf-8")
        for name in [".gitignore", "build.gradle", "go.mod", "apps/web/src/auth/login.ts"]:
            with self.subTest(name=name):
                rows = pl.ph.check_paths(f"{name} 고쳐 줘", self.root)
                self.assertTrue(any(m["mention"] == name and m["exists"] for m in rows), rows)
                self.assertEqual(errors_of(self.p(name), self.root), [])

    def test_no_path_at_all(self):
        self.assertTrue(any("경로가 없습니다" in e for e in errors_of(self.p("로그인 화면"), self.root)))
        self.assertEqual(errors_of(self.p("로그인 화면 (확인 필요)"), self.root), [])


class Verify(unittest.TestCase):
    """검증 칸은 ✗를 내지 않는다 — 모델이 CI 등에서 직접 확인한 명령을 망가뜨리지 않게"""

    def setUp(self):
        self.d, self.root = make_repo(MONOREPO)

    def tearDown(self):
        self.d.cleanup()

    def r(self, verify):
        return result(WEB.format(verify=verify), self.root)

    def test_known_variants_clean(self):
        for v in ["pnpm test", "pnpm run test", "pnpm test:unit", "pnpm lint", "pnpm --filter web test",
                  "pnpm -C apps/web build", "(apps/web에서) pnpm test", "cd apps/web && pnpm test",
                  "pnpm test (루트에서 실행)", "`pnpm test` 실행 후 `Login.test.tsx`가 통과하는지 확인",
                  "테스트 실행: pnpm test", "1) pnpm lint 2) pnpm test", "pnpm lint → pnpm test",
                  "Run: pnpm test"]:
            with self.subTest(v=v):
                r = self.r(v)
                self.assertEqual(r["errors"], [])
                self.assertFalse(any("후보에 없습니다" in w or "명령이 없습니다" in w for w in r["warnings"]), r["warnings"])

    def test_unknown_command_warns_not_errors(self):
        r = self.r("npx tsc --noEmit")
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("후보에 없습니다" in w and "출처" in w for w in r["warnings"]))

    def test_source_must_exist_and_contain_command(self):
        # 출처 파일에 실제로 그 명령이 있을 때만 경고가 사라진다 (지어낸 출처 방지)
        (self.root / ".github/workflows/ci.yml").write_text("steps:\n  - run: npx tsc --noEmit\n", encoding="utf-8")
        self.assertEqual(self.r("npx tsc --noEmit (출처: .github/workflows/ci.yml)")["warnings"], [])
        r = self.r("./gradlew test (출처: .github/workflows/nonexistent.yml)")
        self.assertTrue(any("저장소에 없습니다" in w for w in r["warnings"]))
        r = self.r("make e2e (출처: .github/workflows/ci.yml)")
        self.assertTrue(any("가 없습니다" in w for w in r["warnings"]))

    def test_loose_source_words_do_not_silence(self):
        for v in ["테스트 설정: ./gradlew test", "CI - make e2e"]:
            with self.subTest(v=v):
                self.assertTrue(self.r(v)["warnings"])

    def test_source_is_per_command(self):
        (self.root / "README.md").write_text("pnpm test\n", encoding="utf-8")
        r = self.r("pnpm test, ./gradlew build, make deploy (출처: README.md)")
        flagged = " ".join(r["warnings"])
        self.assertIn("./gradlew build", flagged)
        self.assertIn("make deploy", flagged)

    def test_added_args_warn(self):
        r = self.r("pnpm test src/auth/login.test.ts")
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("인자를 붙였습니다" in w for w in r["warnings"]))

    def test_gradlew_args_not_free_pass(self):
        # 5차 1번: './gradlew' 뒤에 무엇을 써도 통과하던 문제
        d, root = make_repo(EXAMPLE_REPO)
        with d:
            def w(v):
                return result(GOOD.replace("검증: ./gradlew test", f"검증: {v}"), root)["warnings"]
            self.assertEqual(w("./gradlew test"), [])
            for v in ["./gradlew 아무거나Task", "./gradlew testDebugUnitTest --tests LoginTest",
                      './gradlew test --tests "com.x.LoginTest"']:
                with self.subTest(v=v):
                    self.assertTrue(any("인자를 붙였습니다" in x for x in w(v)), w(v))

    def test_source_makefile_target_and_package_scripts(self):
        (self.root / "Makefile").write_text("e2e:\n\techo\n", encoding="utf-8")
        self.assertEqual(self.r("make e2e (출처: Makefile)")["warnings"], [])
        self.assertEqual(self.r("pnpm --filter web build (출처: apps/web/package.json)")["warnings"], [])
        (self.root / ".github/workflows/ci.yml").write_text(
            "run: |\n  pnpm lint \\\n    --max-warnings 0\n", encoding="utf-8")
        self.assertEqual(self.r("pnpm lint --max-warnings 0 (출처: .github/workflows/ci.yml)")["warnings"], [])
        self.assertEqual(self.r("pnpm lint --max-warnings 0 (출처: .github/workflows/ci.yml:10)")["warnings"], [])

    def test_bash_prefixed_script(self):
        (self.root / "scripts").mkdir()
        (self.root / "scripts/check.sh").write_text("", encoding="utf-8")
        self.assertEqual(self.r("bash scripts/check.sh")["warnings"], [])

    def test_trailing_dash_description(self):
        # 6차 새 문제 2: "pnpm test — 설명"이 인자 경고가 되던 문제
        for v in ["pnpm test — 전체 테스트", "pnpm test: 전체 테스트", "pnpm test - 통과해야 함"]:
            with self.subTest(v=v):
                self.assertEqual(self.r(v)["warnings"], [])

    def test_korean_option_value_kept(self):
        d, root = make_repo({"pyproject.toml": "", "tests/test_a.py": "", "src/a.py": ""})
        with d:
            p = WEB.replace("apps/web/src/auth/login.ts", "src/a.py").format(verify="pytest -k 로그인")
            w = " ".join(result(p, root)["warnings"])
            self.assertIn("pytest -k 로그인", w)

    def test_unverified_or_none(self):
        self.assertEqual(self.r("(확인 필요) — 수동 확인")["warnings"], [])
        r = self.r("없음 — 문서 작업이라 검증 명령 없음")
        self.assertEqual(r["errors"], [])

    def test_unverified_explanation_not_command(self):
        # 재시험에서 발견: "(확인 필요) — … tests/에는 …"의 설명을 명령으로 읽었다
        for v in ["(확인 필요) — 저장소에 테스트·린트 설정이 없음. tests/에는 __init__.py만 있음",
                  "(확인 필요): route()를 직접 호출해 확인", "tests/에는 테스트가 없음 (확인 필요)"]:
            with self.subTest(v=v):
                self.assertEqual(self.r(v)["warnings"], [])

    def test_npm_test_equals_npm_run_test(self):
        d, root = make_repo({"package.json": json.dumps({"scripts": {"test": "jest"}}), "src/a.ts": ""})
        with d:
            p = WEB.replace("apps/web/src/auth/login.ts", "src/a.ts")
            for v in ["npm test", "npm run test"]:
                self.assertEqual(result(p.format(verify=v), root)["warnings"], [])

    def test_repo_script(self):
        d, root = make_repo({**EXAMPLE_REPO, "scripts/check.sh": "#!/bin/sh"})
        with d:
            ok = GOOD.replace("검증: ./gradlew test", "검증: ./scripts/check.sh")
            self.assertEqual(result(ok, root)["warnings"], [])
            bad = GOOD.replace("검증: ./gradlew test", "검증: ./scripts/nope.sh")
            self.assertTrue(any("후보에 없습니다" in w for w in result(bad, root)["warnings"]))


class Criteria(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def r(self, item):
        return result(GOOD.replace("잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다", item), self.root)

    def test_strong_unobservable_errors(self):
        for bad in ["로그인이 잘 동작한다", "정상적으로 처리된다", "문제없이 실행된다", "잘 된다",
                    "이상 없이 돌아간다", "올바르게 동작한다"]:
            with self.subTest(bad=bad):
                self.assertTrue(any("확인할 수 없는 표현" in e for e in self.r(bad)["errors"]))

    def test_weak_expressions_only_warn(self):
        for weak in ["코드가 깔끔해진다", "제대로 된 형식이 아니면 400을 돌려준다", "완벽 일치 검색 결과가 1건이다",
                     "코드 품질이 향상된다", "버그가 수정된다"]:
            with self.subTest(weak=weak):
                r = self.r(weak)
                self.assertEqual(r["errors"], [])
                self.assertTrue(any("기준이 흐립니다" in w for w in r["warnings"]))

    def test_quoted_ui_text_ignored(self):
        r = self.r("'입력값이 적절하지 않습니다' 문구가 보인다")
        self.assertEqual((r["errors"], [w for w in r["warnings"] if "흐립니다" in w]), ([], []))
        r = self.r("\"정상적으로 저장되었습니다\" 토스트가 뜬다")
        self.assertEqual(r["errors"], [])
        r = self.r("lint 결과에 문제 없음이 출력된다")
        self.assertEqual(r["errors"], [])


class Other(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def test_constraint_needs_source(self):
        p = GOOD.replace("제약: (요청·저장소 문서에 없음)", "제약: 새 의존성 추가 금지")
        self.assertTrue(any("출처가 없습니다" in w for w in warnings_of(p, self.root)))
        p = GOOD.replace("제약: (요청·저장소 문서에 없음)", "제약: AGENTS.md — 새 의존성 추가 금지")
        self.assertFalse(any("출처" in w for w in warnings_of(p, self.root)))

    def test_constraint_from_request(self):
        for c in ["다른 규칙의 키워드는 바꾸지 않는다 (요청 원문)", "요청 — 다른 규칙의 키워드는 바꾸지 않는다"]:
            with self.subTest(c=c):
                p = GOOD.replace("제약: (요청·저장소 문서에 없음)", f"제약: {c}")
                self.assertFalse(any("출처가 없습니다" in w for w in warnings_of(p, self.root)))

    def test_constraint_source_with_quoted_comma(self):
        # 실행 시험에서 발견: 따옴표 안 쉼표에서 잘려 '출처 없음'이 떴다
        p = GOOD.replace("제약: (요청·저장소 문서에 없음)",
                         '제약: AGENTS.md — 역할 표에서 "Code review, PR review"는 code-reviewer로 간다')
        self.assertFalse(any("출처가 없습니다" in w for w in warnings_of(p, self.root)))

    def test_constraint_placeholder_left(self):
        for c in ["요청 — (요청에 있던 문장 그대로)", "AGENTS.md — (그 문서에 적힌 규칙)",
                  "AGENTS.md — 문서는 한국어로 작성; 요청 — (요청에 있던 문장 그대로)"]:
            with self.subTest(c=c):
                r = result(GOOD.replace("제약: (요청·저장소 문서에 없음)", f"제약: {c}"), self.root)
                self.assertTrue(any("자리표시자" in e for e in r["errors"]), r["errors"])
                self.assertFalse(any("출처가 없습니다" in w for w in r["warnings"]))
        r = result(GOOD.replace("제약: (요청·저장소 문서에 없음)", "제약: AGENTS.md — (문서는 한국어로 작성)"), self.root)
        self.assertFalse(any("자리표시자" in e for e in r["errors"]))

    def test_vague_and_danger_warn_not_error(self):
        p = GOOD.replace("비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", "알아서 고치고 main에 push한다")
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("모호한 표현" in w for w in r["warnings"]))
        self.assertTrue(any("위험 작업 표현" in w and "키워드 기준" in w for w in r["warnings"]))

    def test_multiple_prompts(self):
        bad = GOOD.replace("잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다", "잘 동작한다")
        text = "프롬프트 1/2\n" + GOOD + "\n프롬프트 2/2\n" + bad
        res = pl.lint(text, self.root)["prompts"]
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["errors"], [])
        self.assertTrue(res[1]["errors"])


class NewFormat(unittest.TestCase):
    """SKILL.md 4단계의 새 형식: 요청 원문·상황·작업 규칙, 완료 기준의 '— 확인:'"""

    LINE = "잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다 — 확인: 기존 테스트 app/src/test/java/com/example/auth/LoginViewModelTest.kt"

    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def crit(self, *lines):
        body = "".join(f"  - {l}\n" for l in lines)
        return result(re.sub(r"완료 기준:\n(  - .*\n)+", lambda m: "완료 기준:\n" + body, GOOD), self.root)

    def test_new_required_fields(self):
        for line, name in [("요청 원문: 비밀번호 틀리면 앱이 꺼져\n", "요청 원문"),
                           (GOOD.splitlines()[1] + "\n", "상황"), (RULES, "작업 규칙")]:
            with self.subTest(name=name):
                errs = errors_of(GOOD.replace(line, ""), self.root)
                self.assertTrue(any(f"'{name}:' 칸이 없습니다" in e for e in errs), errs)
        self.assertEqual(errors_of(GOOD, self.root), [])

    def test_request_label_variants(self):
        r = result(GOOD.replace("요청 원문:", "원 요청:").replace("상황:", "배경:"), self.root)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("표준 이름 '요청 원문:'" in w for w in r["warnings"]))

    def test_criteria_needs_check(self):
        r = self.crit("잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다", "검증 명령이 통과한다")
        self.assertTrue(any("확인 방법이 없습니다" in e for e in r["errors"]), r["errors"])
        self.assertEqual(self.crit(self.LINE, "검증 명령이 통과한다")["errors"], [])

    def test_pass_lines_exempt(self):
        for line in ["검증 명령이 통과한다", "./gradlew test가 통과한다", "기존 테스트가 모두 통과한다", "빌드가 통과한다"]:
            with self.subTest(line=line):
                self.assertEqual(self.crit(self.LINE, line)["errors"], [])

    def test_empty_check(self):
        r = self.crit("잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다 — 확인:")
        self.assertTrue(any("'확인:' 뒤가 비었습니다" in e for e in r["errors"]), r["errors"])

    def test_check_part_not_judged(self):
        # '확인' 뒤의 확인 방법 설명에는 '정상적으로' 같은 말이 있어도 된다
        r = self.crit("로그인 화면에 '다시 입력하세요'가 보인다 — 확인: 사람이 직접 — 화면이 정상적으로 뜨는지 본다")
        self.assertEqual(r["errors"], [])
        self.assertFalse(any("흐립니다" in w for w in r["warnings"]))
        r = self.crit("로그인이 정상적으로 된다 — 확인: 사람이 직접 — 로그인해 본다")
        self.assertTrue(any("확인할 수 없는 표현" in e for e in r["errors"]))

    def test_wrapped_criteria_line(self):
        # 목록 표시 없이 이어진 줄은 앞 항목의 계속이다 (오탐 ✗ 방지)
        r = self.crit("잘못된 비밀번호로 로그인해도\n    앱이 종료되지 않는다 — 확인: 기존 테스트 app/src/test/java/com/example/auth/LoginViewModelTest.kt")
        self.assertEqual(r["errors"], [])

    def test_pass_only_warns(self):
        r = self.crit("검증 명령이 통과한다")
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("검증 명령 통과뿐" in w for w in r["warnings"]))
        self.assertFalse(any("통과뿐" in w for w in self.crit(self.LINE)["warnings"]))

    def test_work_rules(self):
        self.assertEqual(errors_of(GOOD, self.root), [])
        rules = list(pl.ph.WORK_RULES)
        dropped = GOOD.replace(RULES, rules_block(rules[:-1]))
        errs = errors_of(dropped, self.root)
        self.assertEqual(len([e for e in errs if "작업 규칙이 형식과 다릅니다" in e]), 1, errs)
        self.assertTrue(any("1개" in e and rules[-1][:25] in e for e in errs))
        changed = GOOD.replace(RULES, rules_block([rules[0].replace("직접 읽고", "읽고")] + rules[1:]))
        self.assertTrue(any("작업 규칙이 형식과 다릅니다" in e for e in errors_of(changed, self.root)))
        extra = GOOD.replace(RULES, rules_block(rules + ["문서는 한국어로 쓴다."]))
        self.assertEqual(errors_of(extra, self.root), [])
        spaced = GOOD.replace(RULES, "작업 규칙:\n" + "".join(f"- {r.replace(' ', '  ')}\n" for r in rules))
        self.assertEqual(errors_of(spaced, self.root), [])     # 불릿·공백 차이는 괜찮다

    def situation(self, text):
        return [w for w in result(GOOD.replace(GOOD.splitlines()[1], f"상황: {text}"), self.root)["warnings"]
                if "상황에 코드 표기" in w]

    def test_situation_code_marks(self):
        for text in ["`LoginViewModel`에서 앱이 꺼진다", "auth/login 화면에서 앱이 꺼진다", "LoginViewModel.kt에서 꺼진다",
                     "loginState가 바뀌지 않는다", "login_state가 비어 있다", "login()을 부르면 꺼진다"]:
            with self.subTest(text=text):
                self.assertEqual(len(self.situation(text)), 1)

    def test_situation_plain_words(self):
        for text in ["메모를 검색할 때 대문자와 소문자가 다르면 결과가 나오지 않는다. \"Todo\"라는 메모가 있어도 \"todo\"로 찾으면 안 나온다.",
                     "로그인 화면에서 비밀번호를 틀리게 넣으면 앱이 꺼진다. 대문자/소문자 구분 없이 찾아져야 한다(검색 화면)."]:
            with self.subTest(text=text):
                self.assertEqual(self.situation(text), [])

    def test_constraint_from_request_text(self):
        def w(c):
            return [x for x in warnings_of(GOOD.replace("제약: (요청·저장소 문서에 없음)", f"제약: {c}"), self.root)
                    if "요청 원문에서 찾을 수 없습니다" in x]
        self.assertTrue(w("요청 — 새 의존성은 추가하지 않는다"))
        self.assertEqual(w("요청 — 비밀번호 틀리면 앱이 꺼져"), [])
        self.assertEqual(w("AGENTS.md — 새 의존성은 추가하지 않는다"), [])
        no_request = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져\n", "").replace(
            "제약: (요청·저장소 문서에 없음)", "제약: 요청 — 새 의존성은 추가하지 않는다")
        self.assertFalse(any("요청 원문에서 찾을 수 없습니다" in x for x in warnings_of(no_request, self.root)))

    def test_danger_ignores_work_rules(self):
        self.assertFalse(any("위험 작업" in w for w in warnings_of(GOOD, self.root)))
        for old, new in [("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", "목표: 버그를 고치고 main에 push한다"),
                         ("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: 비밀번호 틀리면 앱이 꺼져, 고치고 push해 줘")]:
            with self.subTest(new=new):
                self.assertTrue(any("위험 작업" in w and "push" in w for w in warnings_of(GOOD.replace(old, new), self.root)))

    def test_split_on_request_and_header(self):
        res = pl.lint(GOOD + "\n" + GOOD, self.root)["prompts"]
        self.assertEqual(len(res), 2)
        self.assertEqual([r["errors"] for r in res], [[], []])
        res = pl.lint("프롬프트 1/2\n" + GOOD + "프롬프트 2/2\n" + GOOD, self.root)["prompts"]
        self.assertEqual(len(res), 2)
        # 모델이 둘째 프롬프트의 '요청 원문'을 빠뜨려도 다시 나온 '목표'에서 나눈다
        second = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져\n", "")
        res = pl.lint(GOOD + "\n" + second, self.root)["prompts"]
        self.assertEqual(len(res), 2)
        self.assertTrue(any("'요청 원문:' 칸이 없습니다" in e for e in res[1]["errors"]))
        self.assertEqual(res[0]["errors"], [])

    def test_constraint_request_line_is_not_field(self):
        p = GOOD.replace("제약: (요청·저장소 문서에 없음)", "제약:\n- 요청 — 비밀번호 틀리면 앱이 꺼져")
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["fields"]["요청 원문"], "비밀번호 틀리면 앱이 꺼져")
        self.assertIn("요청 —", r["fields"]["제약"])


class Guards(unittest.TestCase):
    """평가에서 나온 문제: 가정 과다, 지어낸 API 주소, 상황의 개발 용어"""

    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def assume(self, n, bullet=True):
        lines = "".join((f"  - 가정 {i}\n" if bullet else f"가정 {i}번\n") for i in range(1, n + 1))
        p = GOOD.replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)\n", "가정:\n" + lines)
        return [w for w in warnings_of(p, self.root) if "가정이" in w]

    def test_too_many_assumptions(self):
        self.assertEqual(self.assume(3), [])
        w = self.assume(4)
        self.assertEqual(len(w), 1)
        self.assertIn("가정이 4개입니다", w[0])
        self.assertTrue(self.assume(4, bullet=False))
        self.assertEqual(pl._count_assumptions("없음"), 0)
        self.assertEqual(pl._count_assumptions("하나만 정함"), 1)

    def test_human_check_must_not_need_dev_tools(self):
        old = "— 확인: 기존 테스트 app/src/test/java/com/example/auth/LoginViewModelTest.kt"
        bad = GOOD.replace(old, "— 확인: 사람이 직접 — 개발자 도구의 네트워크 탭에서 요청이 나가는지 본다")
        w = [x for x in warnings_of(bad, self.root) if "개발 도구" in x]
        self.assertEqual(len(w), 1)
        self.assertIn("개발자 도구", w[0])
        ok = GOOD.replace(old, "— 확인: 사람이 직접 — 틀린 비밀번호를 넣고 로그인 버튼을 눌러 본다")
        self.assertEqual([x for x in warnings_of(ok, self.root) if "개발 도구" in x], [])
        self.assertEqual(errors_of(bad, self.root), [])

    def test_added_test_needs_a_test_file_in_target(self):
        how = "— 확인: 이 경우를 검사하는 테스트를 추가해 ./gradlew test로"
        p = GOOD.replace("— 확인: 기존 테스트 app/src/test/java/com/example/auth/LoginViewModelTest.kt", how)
        w = [x for x in warnings_of(p, self.root) if "테스트 파일이 없습니다" in x]
        self.assertEqual(len(w), 1)
        src = "대상: app/src/main/java/com/example/auth/LoginViewModel.kt"
        with_test = p.replace(src, src + ", app/src/test/java/com/example/auth/LoginViewModelTest.kt")
        self.assertEqual([x for x in warnings_of(with_test, self.root) if "테스트 파일이 없습니다" in x], [])
        self.assertEqual([x for x in warnings_of(GOOD, self.root) if "테스트 파일이 없습니다" in x], [])   # 기존 테스트로 확인

    def git_repo(self, files):
        d, root = make_repo(files)
        (root / ".git").rmdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        return d, root

    def api(self, root, goal):
        p = GOOD.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", f"목표: {goal}")
        return [w for w in warnings_of(p, root) if "찾을 수 없는 주소" in w]

    def test_unknown_api(self):
        d, root = self.git_repo({**EXAMPLE_REPO, "server/routes.py": "@app.post('/api/login')\n"})
        with d:
            w = self.api(root, "주문할 때 POST /orders 응답을 처리한다")
            self.assertEqual(len(w), 1)
            self.assertIn("'POST /orders'", w[0])
            self.assertEqual(self.api(root, "로그인할 때 POST /api/login 응답을 처리한다"), [])
            p = GOOD.replace(GOOD.splitlines()[1], "상황: 로그인하면 GET /api/me 결과가 비어 있다")
            self.assertTrue(any("'GET /api/me'" in w for w in warnings_of(p, root)))

    def check(self, files, goal, request=None):
        d, root = self.git_repo({**EXAMPLE_REPO, **files})
        with d:
            p = GOOD if request is None else GOOD.replace(GOOD.splitlines()[0], f"요청 원문: {request}")
            p = p.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", f"목표: {goal}")
            return [w for w in warnings_of(p, root) if "찾을 수 없는 주소" in w]

    def test_api_in_folder_path_is_not_an_api(self):
        self.assertEqual(len(self.check({"README.md": "주문 내역 (`src/orders`)\n"}, "POST /orders 로 보낸다")), 1)

    def test_api_found_as_address(self):
        goal = "POST /orders 로 보낸다"
        for name, files in [("호출", {"src/api.ts": 'api.post("/orders", body)\n'}),
                            ("템플릿", {"src/a.ts": "fetch(`${BASE}/orders/${id}`)\n"}),
                            ("전체 주소", {"src/a.ts": "const u = 'https://api.example.com/orders'\n"})]:
            with self.subTest(name):
                self.assertEqual(self.check(files, goal), [])
        self.assertEqual(self.check({"docs/a.md": "GET /orders/{id}\n"}, "GET /orders/{id} 로 읽는다"), [])

    def test_api_trailing_punctuation_is_dropped(self):
        files = {"src/api.ts": 'api.post("/orders", body)\n'}
        self.assertEqual(self.check(files, "장바구니를 보낸다. 주소는 POST /orders."), [])
        w = self.check({}, "장바구니를 보낸다. 주소는 POST /orders.")
        self.assertEqual(len(w), 1)
        self.assertIn("'POST /orders'은(는)", w[0])

    def test_api_longer_name_is_not_match(self):
        self.assertEqual(len(self.check({"src/a.ts": 'get("/ordersummary")\n'}, "POST /orders 로 보낸다")), 1)

    def test_api_from_file_path(self):
        self.assertEqual(self.check({"app/api/orders/route.ts": "export {}\n"}, "POST /api/orders 로 보낸다"), [])
        self.assertEqual(self.check({"pages/api/orders.ts": "export {}\n"}, "POST /api/orders 로 보낸다"), [])
        self.assertEqual(len(self.check({"src/orders/OrderList.tsx": "export {}\n"}, "POST /orders 로 보낸다")), 1)

    def test_api_in_request_is_not_checked(self):
        self.assertEqual(self.check({}, "POST /orders 로 보낸다", request="POST /orders 로 보내 줘"), [])
        self.assertEqual(len(self.check({}, "POST /orders 로 보낸다", request="결제 붙여줘")), 1)

    def test_api_with_only_placeholder_is_skipped(self):
        self.assertEqual(self.check({}, "GET /{id} 로 읽는다"), [])

    def test_api_warning_text(self):
        w = self.check({}, "POST /orders 로 보낸다")[0]
        self.assertEqual(w, "'POST /orders'은(는) 저장소에서 찾을 수 없는 주소입니다. 직접 찾아보고, 없으면 지어낸 주소이니 "
                            "프롬프트에서 빼세요. 사용자에게는 주소가 아니라 화면에서 무엇이 보이면 다 된 것인지를 묻습니다")

    def test_api_check_excludes_installed_skill_folder(self):
        d, root = self.git_repo(EXAMPLE_REPO)
        with d:
            dst = root / ".claude" / "skills" / "prompt-builder"
            dst.mkdir(parents=True)
            for n in ("prompt_lint.py", "prompt_helper.py"):
                (dst / n).write_bytes((Path(pl.__file__).resolve().parent / n).read_bytes())
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            p = GOOD.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", "목표: POST /orders 로 보낸다")
            out = subprocess.run([sys.executable, "-B", str(dst / "prompt_lint.py"), "--root", str(root)],
                                 input=p, capture_output=True, text=True, timeout=30).stdout
            self.assertIn("'POST /orders'은(는) 저장소에서 찾을 수 없는 주소", out)

    def test_api_check_skipped_without_git(self):
        self.assertEqual(self.api(self.root, "주문할 때 POST /orders 응답을 처리한다"), [])   # 가짜 .git 폴더
        d = tempfile.TemporaryDirectory()
        with d:
            self.assertEqual(self.api(Path(d.name).resolve(), "주문할 때 POST /orders 응답을 처리한다"), [])

    def tech(self, text):
        return [w for w in result(GOOD.replace(GOOD.splitlines()[1], f"상황: {text}"), self.root)["warnings"]
                if "개발 용어" in w]

    def test_situation_tech_terms(self):
        w = self.tech("로그인할 때 API 호출이 실패해 앱이 꺼진다")
        self.assertEqual(len(w), 1)
        self.assertIn("API", w[0])
        self.assertIn("호출", w[0])
        self.assertTrue(self.tech("목록 컴포넌트가 다시 렌더링된다"))
        self.assertEqual(self.tech("로그인 화면에서 비밀번호를 틀리게 넣으면 앱이 꺼진다"), [])
        self.assertEqual(self.tech("RAPID 버튼을 누르면 '함수 오류' 문구가 뜬다"), [])   # 낱말 경계, 따옴표 안
        r = result(GOOD.replace(GOOD.splitlines()[1], "상황: `login()` 함수에서 꺼진다"), self.root)
        self.assertTrue(any("코드 표기" in x for x in r["warnings"]))
        self.assertTrue(any("개발 용어" in x for x in r["warnings"]))     # 코드 표기와 별개 줄


class ReviewFixes(unittest.TestCase):
    """독립 리뷰에서 재현된 잘못된 ✗, 오래 걸리는 입력, 단서 오탐"""

    def setUp(self):
        self.d, self.root = make_repo({**EXAMPLE_REPO, "app/api/orders/route.ts": "", "src/auth/login.ts": "",
                                       "src/lib/a.ts": ""})

    def tearDown(self):
        self.d.cleanup()

    def target_errors(self, target):
        return errors_of(GOOD.replace("대상: app/src/main/java/com/example/auth/LoginViewModel.kt", f"대상: {target}"),
                         self.root)

    def crit(self, line):
        return GOOD.replace("  - 검증 명령이 통과한다", f"  - {line}")

    # 1
    def test_new_file_mark_wins_over_same_name_elsewhere(self):
        for t in ["app/api/share/route.ts (새 파일), tests/test_search.py", "src/share/login.ts (새 파일)",
                  "src/share/login.ts (확인 필요)"]:
            with self.subTest(t=t):
                self.assertEqual(self.target_errors(t), [])
        self.assertTrue(self.target_errors("src/share/login.ts"))      # 표시가 없으면 여전히 ✗

    # 2
    def test_moved_request_label_does_not_split(self):
        lines = GOOD.splitlines()
        moved = [l for l in lines if not l.startswith("요청 원문:")]
        i = next(n for n, l in enumerate(moved) if l.startswith("검증:"))
        moved.insert(i + 1, lines[0])
        self.assertEqual(len(pl.lint("\n".join(moved), self.root)["prompts"]), 1)
        self.assertEqual(len(pl.lint(GOOD + "\n" + GOOD, self.root)["prompts"]), 2)

    # 3
    def test_indented_header_like_line_is_body(self):
        p = GOOD.replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)",
                         "가정:\n  프롬프트 1/2 결과가 반영됐다고 봄\n  - 다른 가정")
        self.assertEqual(len(pl.lint(p, self.root)["prompts"]), 1)
        p2 = GOOD.replace("참고:", "프롬프트 1/2 결과를 본다\n참고:")       # 문장이 이어지면 머리줄이 아님
        self.assertEqual(len(pl.lint(p2, self.root)["prompts"]), 1)
        self.assertEqual(len(pl.lint(GOOD + "\n프롬프트 2/2\n" + GOOD, self.root)["prompts"]), 2)

    # 4
    def test_code_identifiers_in_target_are_not_paths(self):
        for t in ["notes/search.py (process.env 값 읽는 곳)", "notes/search.py (Express.js 라우터)",
                  "src/lib/a.ts (res.json() 응답 처리), notes/search.py", "notes/search.py의 data.json 처리",
                  "notes/search.py, res.json() 응답"]:
            with self.subTest(t=t):
                self.assertEqual(self.target_errors(t), [])
        self.assertTrue(self.target_errors("notes/nothing.py (설명)"))      # 진짜 없는 경로는 여전히 ✗

    # 5
    def test_line_numbers_and_globs(self):
        for t in ["`notes/search.py:12`, tests/test_search.py", "`notes/search.py:12-20`", "notes/search.py#L3-L9",
                  "tests/test_*.py, notes/search.py", "notes/**/*.html"]:
            with self.subTest(t=t):
                self.assertEqual(self.target_errors(t), [])
        r = result(GOOD.replace("대상: app/src/main/java/com/example/auth/LoginViewModel.kt", "대상: src/**/*.ts"), self.root)
        self.assertEqual(r["errors"], [])
        r = result(GOOD.replace("대상: app/src/main/java/com/example/auth/LoginViewModel.kt", "대상: docs/**/*.zip"), self.root)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("'docs/**/*.zip'에 맞는 파일이 없습니다" in w for w in r["warnings"]))

    # 6
    def test_unobservable_false_positives(self):
        ok = " — 확인: 사람이 직접 — 검색해 본다"
        for line in ["앱이 비정상적으로 꺼지지 않고 검색 결과 화면이 보인다", "중복 메모가 두 개 이상 없다",
                     "중복 메모가 3개 이상 없다"]:
            with self.subTest(line=line):
                self.assertEqual(errors_of(self.crit(line + ok), self.root), [])
        self.assertTrue(errors_of(self.crit("정상적으로 저장된다" + ok), self.root))
        self.assertTrue(errors_of(self.crit("문제없이 보인다" + ok), self.root))
        p = self.crit("pytest가 문제없이 통과한다")
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("문제없이" in w for w in r["warnings"]))

    # 7
    def test_nfd_hangul_file_name(self):
        import unicodedata
        d, root = make_repo({unicodedata.normalize("NFD", "문서/설치.md"): "x"})
        with d:
            self.assertEqual(pl.check_target("설치.md", root)[0], [])
            self.assertEqual(pl.check_target("문서/설치.md", root)[0], [])

    # 8
    def test_check_method_label_and_intro_line(self):
        p = self.crit("앱이 종료되지 않는다 (확인 방법: 사람이 직접 — 틀린 비밀번호를 넣어 본다)")
        self.assertEqual(errors_of(p, self.root), [])
        p = GOOD.replace("완료 기준:\n", "완료 기준: 아래 두 가지\n")
        self.assertEqual(errors_of(p, self.root), [])
        _, _, bodies = pl.check_criteria("아래 두 가지\n- a — 확인: 사람이 직접 — b\n- c — 확인: 사람이 직접 — d")
        self.assertEqual(bodies, ["a", "c"])

    # 9
    def test_long_inputs_finish_quickly(self):
        import time
        t0 = time.time()
        for target in ["A" * 100000, "a/" * 50000, "A " * 50000]:
            lint_text = GOOD.replace("대상: app/src/main/java/com/example/auth/LoginViewModel.kt", "대상: " + target)
            pl.lint(lint_text, self.root)
        self.assertLess(time.time() - t0, 6)
        t0 = time.time()
        p = GOOD.replace("제약: (요청·저장소 문서에 없음)", "제약: (요청" + " 없음" * 6000 + " x))")
        pl.lint(p, self.root)
        self.assertLess(time.time() - t0, 2)

    # 10
    def test_non_utf8_stdin_does_not_crash(self):
        for envx in ({"PYTHONUTF8": "0", "LC_ALL": "C"}, {}):
            for script, data in [(SCRIPT, GOOD.encode() + b"\xff\xfe\x80\n"),
                                 (str(SKILL_DIR / "prompt_helper.py"), b"\xff\xfe\x80 \xea\xb3\xa0\xec\xb3\x90 \xec\xa4\x98")]:
                with self.subTest(script=Path(script).name, env=envx):
                    args = [sys.executable, "-B", script] + (["scan", "--text"] if script != SCRIPT else [])
                    p = subprocess.run(args, input=data, capture_output=True, timeout=30,
                                       env={**ENV, **envx}, cwd=str(self.root))
                    self.assertNotIn(b"Traceback", p.stderr)
                    self.assertIn(p.returncode, (0, 1))

    # 13
    def test_android_app_module_is_not_a_route(self):
        self.assertFalse(pl._api_in_paths("/payments", ["app/src/main/java/com/x/payments/PayRepo.kt"]))
        self.assertTrue(pl._api_in_paths("/api/orders", ["app/api/orders/route.ts"]))
        self.assertTrue(pl._api_in_paths("/api/orders", ["src/app/api/orders/route.ts"]))
        self.assertTrue(pl._api_in_paths("/api/orders", ["pages/api/orders.ts"]))

    # 14
    def test_unverified_note_counts_any_verify_warning(self):
        out = pl.to_text({"root": "x", "prompts": [{"errors": [], "warnings": ["검증에 실행할 명령이 없습니다"]}]})
        self.assertIn("'검증'으로 시작하는 △", out)
        out = pl.to_text({"root": "x", "prompts": [{"errors": [], "warnings": ["가정이 많습니다"]}]})
        self.assertNotIn("'검증'으로 시작하는 △", out)

    # 15
    def server(self, **sub):
        p = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: " + sub.get("request", "결제 붙여줘 / 버튼 누르면 결제됐다고 나오면 돼요"))
        if "situation" in sub:
            p = p.replace(p.splitlines()[1], "상황: " + sub["situation"])
        for old, new in sub.get("replace", []):
            p = p.replace(old, new)
        return [w for w in warnings_of(p, self.root) if "요청 원문에 없는" in w]

    def test_server_not_in_request(self):
        w = self.server(situation="버튼을 누르면 서버에 주문을 보내고 안내가 보여야 한다")
        self.assertEqual(len(w), 1)
        self.assertIn("'서버'이(가) 상황에 있습니다", w[0])
        self.assertTrue(w[0].endswith("빼고 화면에서 보이는 결과만 쓰세요"))
        self.assertEqual(self.server(request="결제 붙여줘. 서버에 보내", situation="버튼을 누르면 서버에 주문을 보낸다"), [])
        self.assertEqual(self.server(replace=[("참고: app", "참고: 서버 문서 app")]), [])
        self.assertEqual(self.server(situation="'서버 오류'라는 문구가 보인다"), [])
        w = self.server(replace=[("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)", "가정: API를 호출하는 것으로 정함")])
        self.assertEqual(len(w), 1)
        self.assertIn("'API'이(가) 가정에 있습니다", w[0])
        self.assertEqual(self.server(situation="API 호출이 실패해 앱이 꺼진다"), [])       # 상황의 API는 개발 용어 △가 낸다
        self.assertEqual(self.server(situation="RAPID 버튼을 누른다"), [])
        w = self.server(situation="서버에 보낸다", replace=[("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)", "가정: API 사용")])
        self.assertEqual(len(w), 1)
        self.assertIn("'서버', 'API'이(가) 상황·가정에 있습니다", w[0])


class ReviewFixes2(unittest.TestCase):
    """재리뷰: 괄호·대괄호 경로, 통과류 줄, 새 기능 단서, 낮은 우선순위 △, 질문 점검, 대상 개수"""

    FILES = {**EXAMPLE_REPO, "app/orders/[id]/page.tsx": "", "app/(shop)/cart/page.tsx": "",
             "app/[...slug]/page.tsx": "", "app/@modal/login/page.tsx": "", "routes/users.$id.tsx": "",
             "node_modules/x/a.ts": "", "src/a/b/c.ts": "", "src/d.ts": ""}

    def setUp(self):
        self.d, self.root = make_repo(self.FILES)

    def tearDown(self):
        self.d.cleanup()

    def res(self, target=None, crit=None):
        p = GOOD
        if target is not None:
            p = p.replace("대상: app/src/main/java/com/example/auth/LoginViewModel.kt", f"대상: {target}")
        if crit is not None:
            p = p.replace("  - 검증 명령이 통과한다", f"  - {crit}")
        return result(p, self.root)

    # A
    def test_paths_inside_or_attached_to_parentheses(self):
        for t in ["검색 화면 (notes/search.py)", "notes/search.py(검색 비교)", "notes/search.py (process.env 값 읽는 곳)",
                  "notes/search.py (Express.js 라우터)", "notes/search.py (D3.js 차트)",
                  "src/d.ts (res.json() 응답 처리)", "notes/search.py (A/B 테스트)"]:
            with self.subTest(t=t):
                self.assertEqual(self.res(t)["errors"], [])
        e = self.res("검색 화면 (notes/serch.py)")["errors"]
        self.assertTrue(any("notes/serch.py" in x and "저장소에 없습니다" in x for x in e))
        self.assertEqual(self.res("src/new.ts (새 파일) (설명)")["errors"], [])

    # B
    def test_bracket_group_at_dollar_paths(self):
        for t in ["app/orders/[id]/page.tsx", "app/(shop)/cart/page.tsx", "app/[...slug]/page.tsx",
                  "app/@modal/login/page.tsx", "routes/users.$id.tsx", "`app/orders/[id]/page.tsx`"]:
            with self.subTest(t=t):
                self.assertEqual(self.res(t)["errors"], [])
        e = self.res("app/orders/[id]/pgae.tsx")["errors"]
        self.assertTrue(any("저장소에 없습니다: app/orders/[id]/pgae.tsx" in x for x in e), e)
        self.assertFalse(any("밖" in x for x in e))

    # C
    def test_pass_line_must_end_with_pass(self):
        r = self.res(crit="테스트가 통과하고 로그인이 정상적으로 동작한다")
        self.assertTrue(any("확인 방법이 없습니다" in x for x in r["errors"]))
        self.assertTrue(any("확인할 수 없는 표현" in x for x in r["errors"]))
        r = self.res(crit="테스트가 통과한 뒤 앱이 정상적으로 동작한다")
        self.assertTrue(any("확인할 수 없는 표현" in x for x in r["errors"]))
        for line in ["기존 테스트가 여전히 통과한다", "검증 명령이 통과함", "빌드가 통과해야 한다", "pytest가 문제없이 통과한다"]:
            with self.subTest(line=line):
                self.assertEqual(self.res(crit=line)["errors"], [])
        self.assertTrue(any("문제없이" in w for w in self.res(crit="pytest가 문제없이 통과한다")["warnings"]))

    # E
    def test_missing_bare_name_next_to_existing_path_warns(self):
        r = self.res("notes/search.py와 serch_util.py")
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("'serch_util.py'은(는) 저장소에 없습니다" in w for w in r["warnings"]))
        r = self.res("notes/search.py (process.env), src/d.ts (res.json())")
        self.assertFalse(any("저장소에 없습니다" in w for w in r["warnings"]))

    # F
    def test_long_word_in_target_warns(self):
        r = self.res("notes/search.py, " + "A" * 300)
        self.assertEqual(sum("200자가 넘는 낱말" in w for w in r["warnings"]), 1)

    # G
    def test_test_path_detection(self):
        for t in ["tests/test_a.py", "src/a.test.ts", "src/a.spec.ts", "e2e/search.cy.ts", "src/a_test.go", "src/a-test.js",
                  "app/src/test/java/LoginViewModelTest.kt", "cypress/x.js", "test_a.py"]:
            with self.subTest(t=t):
                self.assertTrue(pl._has_test_path(t), t)
        for t in ["src/latest/x.py", "src/contest.py", "notes/search.py"]:
            with self.subTest(t=t):
                self.assertFalse(pl._has_test_path(t), t)
        add = "테스트를 추가해 pytest로 돌려 본다"
        w = self.res("src/latest/x.py (새 파일)", crit=f"결과가 보인다 — 확인: {add}")["warnings"]
        self.assertTrue(any("테스트 파일이 없습니다" in x for x in w))
        w = self.res("e2e/search.cy.ts (새 파일)", crit=f"결과가 보인다 — 확인: {add}")["warnings"]
        self.assertFalse(any("테스트 파일이 없습니다" in x for x in w))

    # H
    def test_dev_tool_words(self):
        mk = lambda how: self.res(crit=f"앱이 켜진다 — 확인: 사람이 직접 — {how}")["warnings"]
        self.assertFalse(any("개발 도구" in w for w in mk("터미널에서 실행하고 콘솔에 나온 글을 본다")))
        self.assertTrue(any("개발 도구" in w for w in mk("개발자 도구를 연다")))

    # I
    def test_server_term_boundary_in_request(self):
        p = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: rapid 결제").replace(
            "가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)", "가정: API 사용")
        self.assertTrue(any("'API'이(가) 가정에" in w for w in warnings_of(p, self.root)))

    # K
    def test_glob_matching(self):
        self.assertTrue(pl._glob_hit("src/**/*.ts", self.root))
        self.assertTrue(pl._glob_hit("src/**/c.ts", self.root))
        self.assertTrue(pl._glob_hit("src/d.*", self.root))
        self.assertFalse(pl._glob_hit("**/a.ts", self.root))          # node_modules 안은 보지 않는다
        self.assertFalse(pl._glob_hit("docs/**/*.zip", self.root))

    # L
    def test_criteria_intro_line_only_when_intro(self):
        _, _, bodies = pl.check_criteria("기준은 다음과 같다:\n- a — 확인: 사람이 직접 — b")
        self.assertEqual(bodies, ["a"])
        errs, _, bodies = pl.check_criteria("로그인하면 홈이 보인다\n- 둘째 — 확인: 사람이 직접 — b")
        self.assertEqual(bodies, ["로그인하면 홈이 보인다", "둘째"])
        self.assertTrue(any("확인 방법이 없습니다" in e for e in errs))

    # N
    def git_repo(self, files):
        d, root = make_repo(files)
        (root / ".git").rmdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        return d, root

    def test_api_with_value_slots_checks_fixed_tails(self):
        def warn(files, goal):
            d, root = self.git_repo({**EXAMPLE_REPO, **files})
            with d:
                p = GOOD.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", f"목표: {goal}")
                return [w for w in warnings_of(p, root) if "찾을 수 없는 주소" in w]
        self.assertEqual(warn({"a.py": 'u = "/orders/" + id + "/cancel"\n'}, "POST /orders/{id}/cancel 로 보낸다"), [])
        self.assertEqual(warn({"a.ts": "fetch(`${B}/orders/${id}/cancel`)\n"}, "POST /orders/{id}/cancel 로 보낸다"), [])
        self.assertEqual(len(warn({"a.py": 'u = "/orders"\n'}, "POST /orders/{id}/cancel 로 보낸다")), 1)
        self.assertEqual(len(warn({"a.py": 'u = "/orders"\n'}, "POST /orders/:id/refund 로 보낸다")), 1)
        self.assertEqual(warn({"a.py": 'u = "/orders"\n'}, "GET /orders/{id} 로 읽는다"), [])
        self.assertEqual(warn({}, "GET /{id}/cancel 로 읽는다"), [])

    # O
    def test_question_check(self):
        good = "어느 화면에서 무엇을 했을 때 어떻게 이상한가요? 화면은 메모 목록, 메모 쓰기, 검색, 설정이 있습니다. (예: 검색했는데 결과가 안 나와요)"
        self.assertEqual(pl.check_question(good), [])
        self.assertEqual(pl.question_to_text([]).splitlines(), [
            "질문 점검",
            "→ 점검 끝. 답은 첫 줄에 '먼저 여쭤볼게요'를 쓰고 그다음에 이 질문과 보기만 쓰세요. "
            "그 앞뒤에 저장소를 살펴본 이야기나 묻는 이유를 쓰지 않습니다"])
        w = pl.check_question("무엇이요? 어디요? 언제요? (예: 검색)")
        self.assertEqual(w, ["질문이 3개입니다. 한 번에 하나, 많아야 둘만 물으세요"])
        w = pl.check_question("`src/a.ts`의 formatDate가 문제인가요? (예: 날짜)")
        self.assertEqual(len(w), 1)
        self.assertIn("질문에 코드 표기(", w[0])
        self.assertIn("화면에서 보이는 말로 바꾸세요", w[0])
        w = pl.check_question("어느 API를 호출하나요? (예: 엔드포인트 이름)")
        self.assertEqual(len(w), 1)
        self.assertIn("질문에 개발 용어(API, 호출, 엔드포인트)가 있습니다", w[0])
        self.assertTrue(pl.check_question("백엔드가 문제인가요? (예: 호스팅)")[0].startswith("질문에 개발 용어(백엔드, 호스팅)"))
        self.assertEqual(pl.check_question("'서버 오류' 문구가 뜨나요? (예: 네)"), [])
        w = pl.check_question("어느 화면인가요?")
        self.assertEqual(w, ["보기가 없습니다. 저장소에서 찾은 화면·기능이나 예를 보기로 주세요"])
        self.assertEqual(pl.check_question("어느 화면인가요?\n- 목록\n- 검색"), [])
        self.assertEqual(pl.check_question("어느 화면인가요? 목록 / 검색"), [])
        self.assertTrue(pl.check_question("어느 화면인가요? 목록/검색"))                    # / 하나만으로는 보기가 아니다
        self.assertEqual(len(pl.check_question("무엇이요?? 어디요?! (예: 검색)")), 0)       # 연달아 붙은 물음표는 하나
        out = pl.question_to_text(w)
        self.assertTrue(out.startswith("질문 점검\n  △ 보기가 없습니다"))
        self.assertTrue(out.endswith("→ △를 읽고 맞으면 질문을 고치세요(다시 점검하지 않습니다). 답은 첫 줄에 '먼저 여쭤볼게요'를 쓰고 "
                                     "그다음에 질문과 보기만 쓰세요. 그 앞뒤에 저장소를 살펴본 이야기, 묻는 이유, 점검 결과나 고친 이야기를 쓰지 않습니다"))

    def test_question_cli(self):
        run = lambda data: subprocess.run([sys.executable, "-B", SCRIPT, "--question"], input=data, capture_output=True,
                                          text=True, timeout=30, env=ENV)
        p = run("어느 화면인가요? (예: 검색)")
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith("질문 점검\n"))
        self.assertIn("→ 점검 끝.", p.stdout)
        p = run("   \n")
        self.assertEqual(p.returncode, 2)
        self.assertIn("점검할 질문이 없습니다", p.stderr)

    def test_question_block_in_skill_runs_if_present(self):
        blocks = [b for b in re.findall(r"```bash\n(.*?)```", SKILL_MD, re.S) if "--question" in b]
        for b in blocks:
            cmd = b.replace("(사용자에게 보낼 질문과 보기를 여기에 그대로)", "어느 화면인가요? (예: 검색)")
            d, root = make_repo({})
            with d:
                subprocess.run(["git", "init", "-q"], cwd=root, check=True)
                dst = root / ".agents" / "skills" / "prompt-builder"
                dst.mkdir(parents=True)
                for f in ("prompt_helper.py", "prompt_lint.py"):
                    (dst / f).write_text((SKILL_DIR / f).read_text(encoding="utf-8"), encoding="utf-8")
                p = subprocess.run(["bash", "-c", cmd], cwd=root, capture_output=True, text=True, timeout=30, env=ENV)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("질문 점검", p.stdout)

    # P
    def test_many_targets(self):
        ok = self.res("tests/test_*.py, notes/search.py, notes/static/editor.css")
        self.assertFalse(any("대상이" in w and "개입니다" in w for w in ok["warnings"]))
        four = self.res("notes/search.py, notes/static/editor.css, tests/test_search.py, tests/test_editor.py")
        w = [x for x in four["warnings"] if "대상이 4개입니다" in x]
        self.assertEqual(len(w), 1)
        self.assertIn("어느 것부터 할지 묻습니다", w[0])
        self.assertIn("'넣지 않은 것'에 적습니다", w[0])
        with_new = self.res("notes/search.py (새 파일), notes/static/editor.css, tests/test_search.py (확인 필요), notes/x.py")
        self.assertTrue(any("대상이 4개입니다" in x for x in with_new["warnings"]))


class ReviewFixes3(unittest.TestCase):
    """3차 리뷰: 통과류 끝말, 패키지 이름, 파일 이름에 붙은 영문 설명, 마크다운 링크 등"""

    FILES = {**EXAMPLE_REPO, "package.json": "{}", "src/auth/login.ts": ""}

    def setUp(self):
        self.d, self.root = make_repo(self.FILES)

    def tearDown(self):
        self.d.cleanup()

    def res(self, target=None, crit=None):
        p = GOOD
        if target is not None:
            p = p.replace("대상: app/src/main/java/com/example/auth/LoginViewModel.kt", f"대상: {target}")
        if crit is not None:
            p = p.replace("  - 검증 명령이 통과한다", f"  - {crit}")
        return result(p, self.root)

    # 1
    def test_pass_line_with_suffix(self):
        for line in ["검증 명령이 통과한다 (pytest)", "pytest가 통과하는지 확인한다", "빌드가 통과한다 (./gradlew build)"]:
            with self.subTest(line=line):
                self.assertEqual(self.res(crit=line)["errors"], [])
        for line in ["테스트가 통과하고 로그인이 정상적으로 동작한다", "테스트가 통과한 뒤 화면이 잘 동작한다",
                     "테스트가 통과하며 정상적으로 동작한다", "테스트가 통과한 다음 정상적으로 동작한다"]:
            with self.subTest(line=line):
                e = self.res(crit=line)["errors"]
                self.assertEqual(len(e), 2, e)
        r = self.res(crit="pytest가 문제없이 통과한다")
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("문제없이" in w for w in r["warnings"]))

    # 2
    def test_scoped_package_name_is_not_path(self):
        self.assertEqual(self.res("package.json의 @types/node 버전")["errors"], [])
        self.assertEqual(self.res("package.json의 @types/node 버전")["warnings"], [])
        self.assertTrue(pl.ph.check_paths("package.json의 @types/node 버전", self.root)[0]["exists"])
        self.assertEqual([m["mention"] for m in pl.ph.check_paths("package.json의 @types/node 버전", self.root)],
                         ["package.json"])

    # 3
    def test_english_description_after_file_name(self):
        for t in ["search.py(main logic)", "search.py (main logic)", "notes/search.py(main logic)"]:
            with self.subTest(t=t):
                self.assertEqual(self.res(t)["errors"], [])
        self.assertEqual(self.res("src/auth/login.ts (res.json() 응답 처리), notes/search.py")["errors"], [])
        e = self.res("LoginService.login()")["errors"]
        self.assertTrue(any("경로가 없습니다" in x for x in e))

    # 4
    def test_markdown_link_target(self):
        self.assertEqual(self.res("[검색](notes/search.py)")["errors"], [])
        e = self.res("[검색](notes/serch.py)")["errors"]
        self.assertTrue(any("저장소에 없습니다: notes/serch.py" in x for x in e), e)

    # 5
    def test_missing_path_in_parentheses_is_warning_when_other_path_exists(self):
        r = self.res("notes/search.py (src/old.py에서 옮김)")
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("대상의 괄호 안 'src/old.py'은(는) 저장소에 없습니다. 고칠 파일이라면 괄호 밖에 쓰세요" in w
                            for w in r["warnings"]))
        self.assertTrue(self.res("검색 화면 (notes/serch.py)")["errors"])

    # 6
    def test_env_var_is_not_path(self):
        r = self.res("$HOME/notes 아래 search.py")
        self.assertEqual(r["errors"], [])
        self.assertEqual(pl.ph._candidates("routes/users.$id.tsx"), ["routes/users.$id.tsx"])

    # 7
    def test_express_style_tail_route(self):
        d, root = make_repo({**EXAMPLE_REPO, "a.js": "app.use('/orders', r)\nrouter.post('/:id/cancel')\n"})
        (root / ".git").rmdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        with d:
            p = GOOD.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", "목표: POST /orders/{id}/cancel 로 보낸다")
            self.assertEqual([w for w in warnings_of(p, root) if "찾을 수 없는 주소" in w], [])
            p = p.replace("/cancel", "/refund")
            self.assertEqual(len([w for w in warnings_of(p, root) if "찾을 수 없는 주소" in w]), 1)

    # 8
    def test_folder_and_file_inside_count_once(self):
        r = self.res("notes/ 폴더의 search.py, tests/test_search.py, src/auth/login.ts")
        self.assertFalse(any("개입니다" in w and "대상이" in w for w in r["warnings"]), r["warnings"])

    # 10 (question 쪽은 ReviewFixes2.test_question_check에 함께 있다)


class Cli(unittest.TestCase):
    def run_cli(self, *args, stdin="", cwd=None):
        return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True,
                              text=True, timeout=30, cwd=cwd, env=ENV)

    def test_exit_codes_and_text(self):
        d, root = make_repo()
        with d:
            ok = self.run_cli("--root", str(root), stdin=GOOD)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("판정: 통과", ok.stdout)
            self.assertIn("→ 고칠 것이 있으면 말없이 고친 답을 바로 쓰세요. 다음 글의 첫 줄은 '이렇게 이해했습니다'입니다.",
                          ok.stdout)
            self.assertNotIn("보여 줘도 됩니다", ok.stdout)
            bad = self.run_cli("--root", str(root), stdin=GOOD.replace("종료되지 않는다", "잘 동작한다"))
            self.assertEqual(bad.returncode, 1)
            self.assertIn("✗", bad.stdout)
            self.assertIn("3번까지만", bad.stdout)          # 루프 종료 조건을 출력이 직접 말한다

    def test_json(self):
        d, root = make_repo()
        with d:
            p = self.run_cli("--json", "--root", str(root), stdin=GOOD)
            self.assertEqual(json.loads(p.stdout)["prompts"][0]["errors"], [])

    def test_input_errors(self):
        d, root = make_repo()
        with d:
            p = self.run_cli("--root", str(root), stdin="그냥 문장")
            self.assertEqual(p.returncode, 2)
            self.assertIn("칸 이름:", p.stdout)             # 어떻게 다시 쓸지 알려 준다
            self.assertEqual(self.run_cli("--root", stdin=GOOD).returncode, 2)
            self.assertEqual(self.run_cli("--root", "/nope/nope", stdin=GOOD).returncode, 2)


class SkillContract(unittest.TestCase):
    def test_skill_examples_pass_lint(self):
        # SKILL.md 예시가 스스로 점검을 통과해야 모델이 따라 해도 안전하다
        blocks = [b for b in re.findall(r"```\n(요청 원문:.*?)```", SKILL_MD, re.S)
                  if "(사용자가 쓴 말 그대로" not in b]          # 형식 자체는 제외
        self.assertEqual(len(blocks), 2)
        d, root = make_repo()
        with d:
            for b in blocks:
                with self.subTest(block=b.splitlines()[0]):
                    r = result(b, root)
                    self.assertEqual((r["errors"], r["warnings"]), ([], []))

    def test_criteria_placeholders_in_skill(self):
        for x in pl.CRITERIA_PLACEHOLDERS:
            self.assertIn(x, SKILL_MD)

    def test_template_placeholders_exclude_rules(self):
        self.assertFalse(any("고치기" in x for x in pl.TEMPLATE_PLACEHOLDERS))
        self.assertTrue(pl._is_placeholder("(무엇을 하는지 한 문장)"))

    def test_constraint_placeholders_in_skill(self):
        # lint가 잡는 자리표시자는 SKILL.md 규칙 2의 문구 그대로여야 한다. 예전 예시 문장은 없어야 한다
        for x in pl.CONSTRAINT_PLACEHOLDERS:
            self.assertIn(x, SKILL_MD)
        self.assertNotIn("다른 규칙은 바꾸지 않는다", SKILL_MD)

    def test_loop_rules_in_skill(self):
        self.assertIn("prompt_lint.py", SKILL_MD)
        self.assertIn("모두 3번까지만", SKILL_MD)
        self.assertIn("(새 파일)", SKILL_MD)
        self.assertIn("애매하면 위험 작업으로 본다", SKILL_MD)
        for phrase in ["— 확인:", "실행은 이 스킬의 일이 아니다", "이렇게 이해했습니다"]:
            self.assertIn(phrase, SKILL_MD)
        # 실행 단계는 빠졌다: 프롬프트가 산출물이고 실행은 사용자가 따로 요청할 때만
        for phrase in ["바로 실행", "이 프롬프트로 진행할까요"]:
            self.assertNotIn(phrase, SKILL_MD)

    def test_lint_command_in_skill_runs(self):
        bash_blocks = re.findall(r"```bash\n(.*?)```", SKILL_MD, re.S)
        cmd = next(b for b in bash_blocks if "prompt_lint.py" in b and "--question" not in b)
        cmd = cmd.replace("(보여 줄 답 전체를 여기에 그대로 — 프롬프트는 코드 블록 안에)", GOOD)
        d, root = make_repo()
        with d:
            (root / ".git").rmdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            dst = root / ".agents" / "skills" / "prompt-builder"
            dst.mkdir(parents=True)
            for f in ("prompt_helper.py", "prompt_lint.py"):
                (dst / f).write_text((SKILL_DIR / f).read_text(encoding="utf-8"), encoding="utf-8")
            for shell in ("bash", "zsh"):
                with self.subTest(shell=shell):
                    p = subprocess.run([shell, "-c", cmd], cwd=root / "docs", capture_output=True,
                                       text=True, timeout=30, env=ENV)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertIn("판정: 통과", p.stdout)

    def test_missing_scripts_message(self):
        bash_blocks = re.findall(r"```bash\n(.*?)```", SKILL_MD, re.S)
        d, root = make_repo({})
        with d:
            for b in bash_blocks:
                p = subprocess.run(["bash", "-c", b], cwd=root, capture_output=True, text=True,
                                   timeout=30, env={**ENV, "HOME": str(root)})
                self.assertIn("없음", p.stdout)


if __name__ == "__main__":
    unittest.main()
