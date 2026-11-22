"""prompt_helper 테스트 — 실제로 칠 법한 한국어 요청과 임시 저장소로 확인한다.

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

sys.dont_write_bytecode = True   # 스킬 폴더에 __pycache__가 생겨 함께 복사되지 않게

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "prompt-builder"
sys.path.insert(0, str(SKILL_DIR))

import prompt_helper as ph  # noqa: E402

SCRIPT = str(SKILL_DIR / "prompt_helper.py")
SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def make_repo(files, git=True):
    """{경로: 내용(str 또는 bytes)} 으로 임시 저장소를 만든다"""
    d = tempfile.TemporaryDirectory()
    root = Path(d.name).resolve()
    if git:
        (root / ".git").mkdir()
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return d, root


def cmds(root, targets=()):
    return [c["cmd"] if c["dir"] == "." else f"{c['dir']}:{c['cmd']}"
            for c in ph.detect_verify_commands(root, targets)]


def run_cli(*args, stdin="", cwd=None):
    return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True,
                          text=True, timeout=30, cwd=cwd, env=ENV)


class AnalyzeRequest(unittest.TestCase):
    def test_rough_request_missing_everything(self):
        r = ph.analyze_request("로그인 버그 고쳐 줘 알아서 깔끔하게")
        self.assertTrue(r["is_task"])
        self.assertEqual(r["missing_fields"], ["대상", "완료 기준", "검증", "제약"])
        self.assertEqual(r["vague"], ["알아서", "깔끔하게"])
        self.assertEqual(r["danger"], [])

    def test_complete_request(self):
        r = ph.analyze_request(
            "대상: src/auth/Login.kt\n완료 기준: 에러 문구가 보여야 함\n"
            "검증: ./gradlew test\n제약: 공개 API 유지\n로그인 실패 처리 수정"
        )
        self.assertEqual(r["missing_fields"], [])

    def test_conjugations_and_soft_requests(self):
        for t in ["버그 고치고 테스트 추가", "화면 만들어 줘", "어제 고쳤던 거 다시 봐 줘",
                  "쿠폰 중복 막아 줘", "이 함수 좀 봐 줘", "에러 나면 토스트 띄우게 해 줘"]:
            with self.subTest(t=t):
                self.assertTrue(ph.analyze_request(t)["is_task"])

    def test_question_is_not_task(self):
        self.assertFalse(ph.analyze_request("Riverpod랑 Provider 차이가 뭐야?")["is_task"])

    def test_known_false_positives_fixed(self):
        self.assertIn("검증", ph.analyze_request("테스트 화면 만들어 줘")["missing_fields"])
        self.assertIn("검증", ph.analyze_request("장바구니 고쳐 줘 cart_screen.dart 알아서")["missing_fields"])
        self.assertIn("제약", ph.analyze_request("유지보수 편하게 리팩토링")["missing_fields"])
        self.assertNotIn("대상", ph.analyze_request("결제 페이지 느려, 개선 부탁")["missing_fields"])
        self.assertNotIn("검증", ph.analyze_request("고쳐 줘. 검증은 flutter test")["missing_fields"])

    def test_korean_particles_on_english_keywords(self):
        # 조사가 붙어도 잡혀야 한다 (파이썬 \b는 한글을 글자로 봐서 놓친다)
        for t, word in [("수정하고 main에 push해줘", "push"), ("고치고 merge해 줘", "merge"),
                        ("production에 반영", "production"), ("PR 올려 줘", "PR"),
                        ("커밋하고 끝내", "커밋"), ("운영에 반영해 줘", "운영에"),
                        ("테이블 삭제해 줘", "삭제"), ("force로 덮어써", "force")]:
            with self.subTest(t=t):
                self.assertIn(word.lower(), [d.lower() for d in ph.analyze_request(t)["danger"]])
        self.assertNotIn("검증", ph.analyze_request("CI에서 깨지는 거 고쳐 줘")["missing_fields"])

    def test_danger_not_on_unrelated_words(self):
        for t in ["프로젝트 구조 설명해 줘", "pushdown 최적화 조사", "운영체제별 차이 정리해 줘"]:
            with self.subTest(t=t):
                self.assertEqual(ph.analyze_request(t)["danger"], [])

    def test_too_big(self):
        text = "\n".join(f"{i}. 기능 {i} 추가" for i in range(1, 8))
        self.assertTrue(ph.analyze_request(text)["too_big"])


class Paths(unittest.TestCase):
    def test_particles_attached(self):
        d, root = make_repo({"src/auth/Login.kt": ""})
        with d:
            by = {p["mention"]: p for p in ph.check_paths("Login.kt에서 고쳐 줘, src/auth/Login.kt를 봐", root)}
            self.assertEqual(by["Login.kt"]["found"], ["src/auth/Login.kt"])
            self.assertTrue(by["src/auth/Login.kt"]["exists"])

    def test_found_by_name_skips_node_modules(self):
        d, root = make_repo({"app/src/main/auth/LoginViewModel.kt": "",
                             "node_modules/x/LoginViewModel.kt": ""})
        with d:
            r = ph.check_paths("LoginViewModel.kt 고쳐 줘", root)
            self.assertEqual(r[0]["found"], ["app/src/main/auth/LoginViewModel.kt"])

    def test_hidden_dirs_and_case(self):
        d, root = make_repo({".github/workflows/ci.yml": "", "src/Login.kt": ""})
        with d:
            by = {p["mention"]: p for p in ph.check_paths("ci.yml 고치고 login.kt도 봐 줘", root)}
            self.assertEqual(by["ci.yml"]["found"], [".github/workflows/ci.yml"])
            self.assertEqual(by["login.kt"]["found"], ["src/Login.kt"])

    def test_helper_hangul_noise(self):
        d, root = make_repo({"docs/guide/설치.md": "", "Dockerfile": "", "설정2.json": ""})
        with d:
            ms = [p["mention"] for p in ph.check_paths(
                "Dockerfile이랑 docs/guide/설치.md도 보고 설정2.json 고치고 dockerfile 다시, 없는파일.kt도", root)]
            self.assertEqual([m.lower() for m in ms].count("dockerfile"), 1)   # 대소문자 달라도 한 번
            self.assertNotIn("docs/guide/", ms)
            self.assertNotIn("2.json", ms)
            self.assertIn("설정2.json", ms)
            self.assertNotIn("response.data", [p["mention"] for p in ph.check_paths("response.data를 읽어", root)])
            self.assertIn("없는파일.kt", ms)          # 없는 한글 파일도 '못 찾음'으로 보고

    def test_hangul_scan_is_fast(self):
        import time
        files = {f"m{i}/f{j}.txt": "" for i in range(100) for j in range(100)}   # 1만 개
        d, root = make_repo(files)
        with d:
            text = " ".join(f"설정{i}.json에서는" for i in range(80))
            t = time.time()
            ph.scan(text, root)
            self.assertLess(time.time() - t, 3.0)

    def test_noise_is_not_path(self):
        d, root = make_repo({})
        with d:
            text = ("https://example.com/a/b 참고해서 입력/출력 처리 고쳐 줘. Node.js e.g. a@b.com "
                    "`flutter test` 통과하게, Flutter 3.32.8로")
            mentions = [p["mention"] for p in ph.check_paths(text, root)]
            for bad in ["//example.com", "a/b", "입력/출력", "Node.js", "e.g", "b.com", "flutter test", "3.32.8"]:
                self.assertNotIn(bad, mentions)

    def test_outside_root_and_long_backtick(self):
        d, root = make_repo({})
        with d:
            self.assertEqual(ph.check_paths("`/etc/hosts` 봐 줘 ../secret.txt", root), [])
            long_tick = "`" + "x" * 300 + "`"
            self.assertEqual(ph.check_paths(long_tick + " 에러 고쳐 줘", root), [])

    def test_max_matches(self):
        d, root = make_repo({f"m{i}/index.ts": "" for i in range(8)})
        with d:
            self.assertEqual(len(ph.check_paths("index.ts 고쳐 줘", root)[0]["found"]), ph.MAX_PATH_MATCHES)


class VerifyCommands(unittest.TestCase):
    def test_runners(self):
        for lock, runner in [("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                             ("bun.lock", "bun run"), (None, "npm run")]:
            files = {"package.json": json.dumps({"scripts": {"test": "vitest", "lint": "eslint .", "dev": "vite"}})}
            if lock:
                files[lock] = ""
            d, root = make_repo(files)
            with d, self.subTest(runner=runner):
                self.assertEqual(cmds(root), [f"{runner} lint", f"{runner} test"])

    def test_npm_default_test_excluded(self):
        d, root = make_repo({"package.json": json.dumps(
            {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})})
        with d:
            self.assertEqual(cmds(root), [])

    def test_broken_configs_do_not_crash(self):
        cases = {
            "null scripts": {"package.json": '{"scripts": null}'},
            "array": {"package.json": "[1,2]"},
            "invalid json": {"package.json": "{"},
            "bom": {"package.json": "﻿" + json.dumps({"scripts": {"test": "jest"}})},
            "euc-kr pubspec": {"pubspec.yaml": "# 주석\nflutter:\n  sdk: flutter\n".encode("euc-kr")},
            "euc-kr makefile": {"Makefile": "# 주석\ntest:\n\techo\n".encode("euc-kr")},
        }
        for name, files in cases.items():
            d, root = make_repo(files)
            with d, self.subTest(name=name):
                result = cmds(root)
                if name == "bom":
                    self.assertEqual(result, ["npm run test"])
                if name == "euc-kr makefile":
                    self.assertEqual(result, ["make test"])

    def test_other_stacks(self):
        stacks = [
            ({"gradlew": "#!/bin/sh"}, ["./gradlew test", "./gradlew build"]),
            ({"pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n", "test/a_test.dart": ""},
             ["flutter analyze", "flutter test"]),
            ({"pubspec.yaml": "name: x\n"}, ["dart analyze"]),
            ({"pyproject.toml": "", "uv.lock": "", "tests/test_a.py": ""}, ["uv run pytest"]),
            ({"pom.xml": ""}, ["mvn test"]),
            ({"Cargo.toml": ""}, ["cargo test", "cargo clippy"]),
            ({"go.mod": ""}, ["go test ./...", "go vet ./..."]),
            ({"Makefile": "lint:\n\techo\ntest:\n\techo\nrun:\n\techo\n"}, ["make lint", "make test"]),
            ({}, []),
        ]
        for files, expected in stacks:
            d, root = make_repo(files)
            with d, self.subTest(files=list(files)):
                self.assertEqual(cmds(root), expected)

    def test_pytest_needs_real_tests(self):
        # 실행 시험에서 발견: tests/에 __init__.py만 있어도 pytest를 후보로 냈다
        d, root = make_repo({"pyproject.toml": "", "tests/__init__.py": ""})
        with d:
            self.assertEqual(cmds(root), [])
        d, root = make_repo({"pyproject.toml": "", "tests/unit/test_router.py": ""})
        with d:
            self.assertEqual(cmds(root), ["pytest"])

    def test_monorepo_nearest_project(self):
        d, root = make_repo({
            "package.json": json.dumps({"private": True}),
            "pnpm-lock.yaml": "",
            "apps/web/package.json": json.dumps({"scripts": {"test": "vitest"}}),
            "apps/web/src/Cart.tsx": "",
        })
        with d:
            # 하위 폴더엔 lock 파일이 없어도 루트의 pnpm-lock.yaml을 따른다
            self.assertEqual(cmds(root, ["apps/web/src/Cart.tsx"]), ["apps/web:pnpm test"])
            r = ph.scan("Cart.tsx 고쳐 줘", root)
            self.assertEqual(r["verify_commands"], [{"dir": "apps/web", "cmd": "pnpm test"}])


class Cli(unittest.TestCase):
    def test_heredoc_with_quotes_and_backticks(self):
        d, root = make_repo({"gradlew": "", "src/Login.kt": ""})
        with d:
            p = run_cli("scan", "--root", str(root), stdin="`Login.kt`에서 \"비밀번호\" 에러 '문구' 고쳐 줘 $HOME")
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads(p.stdout)
            self.assertTrue(data["is_task"])
            self.assertIn({"dir": ".", "cmd": "./gradlew test"}, data["verify_commands"])
            self.assertEqual(data["paths"][0]["found"], ["src/Login.kt"])

    def test_positional_request(self):
        d, root = make_repo({})
        with d:
            p = run_cli("scan", "--text", "--root", str(root), "로그인 버그 고쳐 줘")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("요청에 빠진 항목: 대상, 완료 기준, 검증, 제약", p.stdout)

    def test_input_errors(self):
        d, root = make_repo({})
        with d:
            self.assertEqual(run_cli("scan", "--root", str(root), stdin="").returncode, 2)
            self.assertEqual(run_cli("scan", "--root", stdin="x").returncode, 2)
            p = run_cli("scan", "--root", "/nope/nope", stdin="Login.kt 고쳐")
            self.assertEqual(p.returncode, 2)
            self.assertIn("루트", p.stderr)

    def test_root_from_git_toplevel(self):
        d, root = make_repo({"gradlew": "", "pkg/sub/a.txt": ""}, git=False)
        with d:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            p = run_cli("scan", "--text", stdin="고쳐 줘", cwd=root / "pkg" / "sub")
            self.assertIn(f"저장소 루트: {root}", p.stdout)
            self.assertIn("./gradlew test", p.stdout)

    def test_text_output_lines(self):
        d, root = make_repo({}, git=False)
        with d:
            text = "알아서 고쳐 줘 그리고 main에 push\n" + "\n".join(f"{i}. 기능 {i} 추가" for i in range(1, 8))
            p = run_cli("scan", "--text", "--root", str(root), stdin=text)
            out = p.stdout
            self.assertIn("git 저장소 루트가 아님", out)
            self.assertIn("모호한 표현: 알아서", out)
            self.assertIn("위험 작업 표현: push", out)
            self.assertIn("작업이 큼(목록 7개)", out)
            self.assertIn("검증 명령 후보: 찾지 못함", out)
            p = run_cli("scan", "--text", "--root", str(root), stdin="가" * 4100 + " 고쳐 줘")
            self.assertIn("작업이 큼(", p.stdout)
            self.assertIn("자)", p.stdout)

    def test_template(self):
        p = run_cli("template")
        for k in ["목표:", "대상:", "완료 기준:", "검증:", "제약:", "가정:"]:
            self.assertIn(k, p.stdout)


class SkillContract(unittest.TestCase):
    """SKILL.md와 보조 스크립트가 서로 어긋나지 않는지"""

    def test_frontmatter(self):
        self.assertTrue(SKILL_MD.startswith("---\n"))
        front = SKILL_MD.split("---", 2)[1]
        self.assertIn("name: prompt-builder", front)
        self.assertIn("description:", front)
        self.assertIn('triggers: ["user"]', front)
        self.assertNotIn("- exec", front)       # exec는 모든 셸 명령을 무확인으로 만든다
        self.assertIn("$ARGUMENTS", SKILL_MD)

    def test_template_matches_helper(self):
        self.assertIn(ph.TEMPLATE, SKILL_MD)

    def test_output_labels_documented(self):
        for label in ph.LABELS:
            self.assertIn(label, SKILL_MD, f"SKILL.md 1단계 표에 '{label}' 줄 설명이 없음")

    def test_skill_danger_list_caught_by_script(self):
        # SKILL.md 4단계 위험 작업 목록의 단어를 스크립트도 잡아야 한다
        for word in ["push", "force", "reset --hard", "rm -rf", "커밋", "PR", "머지", "배포",
                     "릴리스", "publish", "production", "삭제", "drop", "truncate", "마이그레이션"]:
            with self.subTest(word=word):
                self.assertTrue(ph.analyze_request(f"{word}해 줘")["danger"], word)

    def test_heredoc_delimiter_consistent(self):
        self.assertIn("<<'PROMPT_BUILDER_EOF'", SKILL_MD)
        self.assertNotIn("<<'EOF'", SKILL_MD)

    def test_examples_follow_rules(self):
        # 예시 프롬프트가 출처 없는 제약을 지어내지 않는지
        blocks = re.findall(r"```\n(목표:.*?)```", SKILL_MD, re.S)
        self.assertGreaterEqual(len(blocks), 2)
        for b in blocks:
            constraint = re.search(r"^제약:(.*)$", b, re.M).group(1)
            self.assertTrue("없음" in constraint or "—" in constraint, constraint)
            self.assertIn("가정:", b)

    def test_skill_command_runs(self):
        # SKILL.md 1단계의 셸 명령을 실제 설치 형태로 돌려 본다
        cmd = re.search(r"```bash\n(.*?)```", SKILL_MD, re.S).group(1)
        cmd = cmd.replace("(요청 원문을 여기에 그대로)", "Login.kt에서 push해줘")
        d, root = make_repo({"src/Login.kt": "", "gradlew": ""}, git=False)
        with d:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            dst = root / ".agents" / "skills" / "prompt-builder"
            dst.mkdir(parents=True)
            (dst / "prompt_helper.py").write_text(Path(SCRIPT).read_text(encoding="utf-8"), encoding="utf-8")
            p = subprocess.run(["bash", "-c", cmd], cwd=root / "src", capture_output=True, text=True,
                               timeout=30, env=ENV)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("src/Login.kt", p.stdout)
            self.assertIn("위험 작업 표현: push", p.stdout)


if __name__ == "__main__":
    unittest.main()
