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

GOOD = """목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다
대상: app/src/main/java/com/example/auth/LoginViewModel.kt
완료 기준:
  - 잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다
  - 검증 명령이 통과한다
검증: ./gradlew test
제약: (요청·저장소 문서에 없음)
가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)
참고: app/src/test/java/com/example/auth/LoginViewModelTest.kt
"""

WEB = """목표: 로그인 실패 메시지를 바꾼다
대상: apps/web/src/auth/login.ts
완료 기준:
  - 비밀번호가 틀리면 '비밀번호를 확인하세요'가 보인다
검증: {verify}
제약: 없음
가정: 없음
"""


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
        p = GOOD.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", "목표: (무엇을, 왜 — 한두 문장)")
        self.assertTrue(any("자리표시자" in e for e in errors_of(p, self.root)))

    def test_label_variants_accepted_with_hint(self):
        p = (GOOD.replace("완료 기준:", "완료기준:").replace("검증:", "검증 방법:")
             .replace("제약:", "제약 사항:"))
        r = result(p, self.root)
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("표준 이름 '완료 기준:'" in w for w in r["warnings"]))

    def test_heading_labels(self):
        p = re.sub(r"^(목표|대상|검증|제약|가정|참고): ", r"## \1\n", GOOD, flags=re.M).replace("완료 기준:", "## 완료 기준")
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

    def test_multiline_placeholder_in_criteria(self):
        p = GOOD.replace("  - 검증 명령이 통과한다", "  - (눈으로 확인할 수 있는 결과 1)")
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
            self.assertIn("사용자에게 보여 줘도 됩니다", ok.stdout)
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
        blocks = [b for b in re.findall(r"```\n(목표:.*?)```", SKILL_MD, re.S)
                  if "(무엇을, 왜" not in b]          # 템플릿 자체는 제외
        self.assertGreaterEqual(len(blocks), 2)
        d, root = make_repo()
        with d:
            for b in blocks:
                with self.subTest(block=b.splitlines()[0]):
                    self.assertEqual(errors_of(b, root), [])

    def test_loop_rules_in_skill(self):
        self.assertIn("prompt_lint.py", SKILL_MD)
        self.assertIn("모두 3번까지만", SKILL_MD)
        self.assertIn("(새 파일)", SKILL_MD)
        self.assertIn("애매하면 위험 작업으로 본다", SKILL_MD)
        self.assertIn("`검증:`에 `(확인 필요)`가 없다", SKILL_MD)     # 바로 실행은 검증 방법이 있을 때만

    def test_lint_command_in_skill_runs(self):
        bash_blocks = re.findall(r"```bash\n(.*?)```", SKILL_MD, re.S)
        cmd = next(b for b in bash_blocks if "prompt_lint.py" in b)
        cmd = cmd.replace("(만든 프롬프트 전체를 여기에 그대로)", GOOD)
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
