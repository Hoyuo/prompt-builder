"""영어 요청·영어 형식 지원 테스트 — helper가 영어 요청의 단서를 뽑고, lint가 영어 형식을 같은 기준으로 점검하는지.

실행: python3 -m unittest discover -s tests
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_prompt_lint import ENV, GOOD, SKILL_DIR, SKILL_MD, make_repo, result  # noqa: E402

import prompt_helper as ph  # noqa: E402
import prompt_lint as pl  # noqa: E402

RULES_EN = "Work rules:\n" + "".join(f"  - {r}\n" for r in ph.WORK_RULES_EN)

GOOD_EN = """Original request: the app closes when the password is wrong
Situation: On the login screen, entering a wrong password closes the app. It should stay open so the user can try again.
Goal: Fix the app closing when the password is wrong
Targets: app/src/main/java/com/example/auth/LoginViewModel.kt
Done when:
  - Entering a wrong password keeps the app open — check: existing test app/src/test/java/com/example/auth/LoginViewModelTest.kt
  - Tests pass
Verify: ./gradlew test
Constraints: (none in the request or repo docs)
Assumptions: none
""" + RULES_EN


def en_examples():
    return [b for b in re.findall(r"```\n(Original request:.*?)```", SKILL_MD, re.S)
            if "(the user's exact words" not in b]


class HelperEnglish(unittest.TestCase):
    def a(self, text):
        return ph.analyze_request(text)

    def test_pointers(self):
        for t in ["this is broken", "fix it", "It crashes when I log in", "something is off on the settings page",
                  "same as last time", "that doesn't work"]:
            with self.subTest(t=t):
                self.assertTrue(self.a(t)["pointers"], t)

    def test_pointer_with_noun_is_not_pointer(self):
        # "this page"처럼 뒤에 이름이 오면 가리키는 대상이 있다. "make it faster"의 it은 요청 안에서 풀린다
        for t in ["fix the bug in this page", "the search page is slow, make it faster", "there is a typo in README.md"]:
            with self.subTest(t=t):
                self.assertEqual(self.a(t)["pointers"], [], t)

    def test_open_ended(self):
        for t in ["make it look nicer", "the search is slow", "clean up the settings page", "improve the editor",
                  "make login faster"]:
            with self.subTest(t=t):
                self.assertTrue(self.a(t)["open_ended"], t)

    def test_new_feature(self):
        for t in ["add a share button", "can you add dark mode", "please implement export",
                  "I want a new export feature", "make a settings page", "Let's build a sign-up screen"]:
            with self.subTest(t=t):
                self.assertTrue(self.a(t)["new_feature"], t)

    def test_new_feature_false_positives(self):
        # 과거형 버그 설명, make it ~은 새로 만드는 요청이 아니다
        for t in ["I added a share button but it does not work", "make it faster", "the added rows disappear",
                  "the export we built yesterday crashes"]:
            with self.subTest(t=t):
                self.assertEqual(self.a(t)["new_feature"], [], t)

    def test_english_cues_on_korean_requests(self):
        # 한국어 요청에 섞인 영어 화면 문구·에러 메시지는 영어 단서로 보지 않는다
        for t in ["회원가입에서 'This field is required'가 계속 떠", "앱 켜면 Something went wrong 이라고 나와",
                  "Add to cart 버튼이 안 눌려", "빌드하면 에러 나요\nBuild failed"]:
            with self.subTest(t=t):
                r = self.a(t)
                self.assertEqual(r["lang"], "ko")
                self.assertEqual((r["pointers"], r["open_ended"], r["new_feature"], r["vague"]), ([], [], [], []), t)
        r = self.a("Login.kt에서 push해줘")
        self.assertEqual((r["lang"], r["danger"]), ("ko", ["push"]))      # 위험 작업은 두 언어 모두 본다

    def test_bug_report_shapes(self):
        # 문장 첫머리의 동사가 이름인 버그 설명, 관계사 that, 따옴표 안 에러 문구
        for t in ["Build fails on CI", "Add button doesn't work", "Create account page crashes",
                  "the note that was deleted still shows", "notes that are pinned disappear",
                  "signup shows 'This field is required'"]:
            with self.subTest(t=t):
                r = self.a(t)
                self.assertEqual((r["pointers"], r["new_feature"]), ([], []), t)
        self.assertNotIn("제약", self.a("it crashes without reloading")["present_fields"])
        self.assertIn("제약", self.a("keep the API unchanged and fix the search")["present_fields"])
        self.assertTrue(self.a("please add retry. the upload fails")["new_feature"])

    def test_language_line(self):
        d, root = make_repo({})
        with d:
            out = ph.to_text(ph.scan("fix the login bug", root))
            self.assertIn("요청 언어: 영어", out)
            self.assertNotIn("요청 언어", ph.to_text(ph.scan("로그인 버그 고쳐 줘", root)))

    def test_vague_and_danger(self):
        self.assertTrue(self.a("just do whatever you think")["vague"])
        self.assertTrue(self.a("do it however, as you see fit")["vague"])
        for t in ["push to main", "deploy to production", "delete the old users table", "go live tonight"]:
            with self.subTest(t=t):
                self.assertTrue(self.a(t)["danger"], t)
        self.assertEqual(self.a("the pusher animation is slow")["danger"], [])

    def test_fields_and_task(self):
        r = self.a("Fix the typo in README.md, don't touch other files")
        self.assertTrue(r["is_task"])
        self.assertIn("대상", r["present_fields"])
        self.assertIn("제약", r["present_fields"])
        r = self.a("search should show notes regardless of case, run the tests to verify")
        self.assertIn("완료 기준", r["present_fields"])
        self.assertIn("검증", r["present_fields"])

    def test_korean_unchanged(self):
        r = self.a("이거 좀 이상해")
        self.assertEqual(r["pointers"], ["이거"])
        self.assertEqual(self.a("이 화면 버그 고쳐 줘")["pointers"], [])

    def test_template_cli(self):
        p = subprocess.run([sys.executable, str(SKILL_DIR / "prompt_helper.py"), "template", "--en"],
                           capture_output=True, text=True, timeout=30, env=ENV)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), ph.TEMPLATE_EN.strip())


class LintEnglish(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def r(self, prompt):
        return result(prompt, self.root)

    def crit(self, line, extra=""):
        return self.r(GOOD_EN.replace(
            "  - Entering a wrong password keeps the app open — check: existing test "
            "app/src/test/java/com/example/auth/LoginViewModelTest.kt", "  - " + line) + extra)

    def test_good_passes(self):
        r = self.r(GOOD_EN)
        self.assertEqual((r["errors"], r["warnings"], r["lang"]), ([], [], "en"))
        self.assertEqual(self.r(GOOD)["lang"], "ko")

    def test_missing_field_uses_english_name(self):
        r = self.r(GOOD_EN.replace("Verify: ./gradlew test\n", ""))
        self.assertIn("'Verify:' 칸이 없습니다", r["errors"])

    def test_label_variant(self):
        r = self.r(GOOD_EN.replace("Done when:", "Acceptance criteria:"))
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("'Done when:'" in w for w in r["warnings"]), r["warnings"])

    def test_check_mark(self):
        r = self.crit("The app stays open")
        self.assertTrue(any("'— check: …'" in e for e in r["errors"]), r["errors"])
        r = self.crit("The app stays open — check:")
        self.assertTrue(any("'check:' 뒤가 비었습니다" in e for e in r["errors"]), r["errors"])

    def test_unobservable(self):
        r = self.crit("Login works correctly — check: manual — log in")
        self.assertTrue(any("확인할 수 없는 표현" in e for e in r["errors"]), r["errors"])
        r = self.crit("Login finishes without any issues — check: manual — log in")
        self.assertTrue(any("확인할 수 없는 표현" in e for e in r["errors"]), r["errors"])
        r = self.crit("Login is handled properly — check: manual — log in")
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("흐립니다" in w for w in r["warnings"]), r["warnings"])

    def test_quoted_screen_text_and_apostrophes(self):
        # 인용한 화면 문구는 판정하지 않는다. don't의 아포스트로피는 따옴표가 아니다
        r = self.crit("The screen shows 'Works correctly' after saving — check: manual — save a note")
        self.assertEqual(r["errors"], [])
        r = self.crit("The app doesn't close and the user's input works correctly — check: manual — log in")
        self.assertTrue(any("확인할 수 없는 표현" in e for e in r["errors"]), r["errors"])

    def test_manual_with_dev_tools(self):
        r = self.crit("The app stays open — check: manual — open devtools and watch the network tab")
        self.assertTrue(any("개발 도구" in w for w in r["warnings"]), r["warnings"])

    def test_add_test_needs_test_target(self):
        r = self.crit("The app stays open — check: add a test for this case and run ./gradlew test")
        self.assertTrue(any("테스트 파일이 없습니다" in w for w in r["warnings"]), r["warnings"])

    def test_target_marks(self):
        base = GOOD_EN.replace("Targets: app/src/main/java/com/example/auth/LoginViewModel.kt", "Targets: {}")
        r = self.r(base.format("src/auth/logout.ts"))
        self.assertTrue(any("'(new file)'" in e and "'(needs check)'" in e for e in r["errors"]), r["errors"])
        r = self.r(base.format("app/src/main/java/com/example/auth/Logout.kt (new file)"))
        self.assertEqual(r["errors"], [])
        r = self.r(base.format("login screen (needs check)"))
        self.assertEqual(r["errors"], [])

    def test_verify_source(self):
        r = self.r(GOOD_EN.replace("Verify: ./gradlew test", "Verify: npm test"))
        self.assertTrue(any("(source:" in w for w in r["warnings"]), r["warnings"])
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / ".github" / "workflows" / "ci.yml").write_text("run: npm test\n", encoding="utf-8")
        r = self.r(GOOD_EN.replace("Verify: ./gradlew test", "Verify: npm test (source: .github/workflows/ci.yml)"))
        self.assertFalse(any(w.startswith("검증") for w in r["warnings"]), r["warnings"])
        r = self.r(GOOD_EN.replace("Verify: ./gradlew test", "Verify: (needs check) — no test setup found"))
        self.assertFalse(any(w.startswith("검증") for w in r["warnings"]), r["warnings"])

    def test_constraints(self):
        r = self.r(GOOD_EN.replace("Constraints: (none in the request or repo docs)", "Constraints: keep the old login flow"))
        self.assertTrue(any("출처가 없습니다" in w for w in r["warnings"]), r["warnings"])
        r = self.r(GOOD_EN.replace("Constraints: (none in the request or repo docs)",
                                   "Constraints: request — do not modify the database schema"))
        self.assertTrue(any("요청 원문에서 찾을 수 없습니다" in w for w in r["warnings"]), r["warnings"])
        r = self.r(GOOD_EN.replace("Constraints: (none in the request or repo docs)",
                                   "Constraints: request — the password is wrong"))
        self.assertEqual(r["warnings"], [])
        r = self.r(GOOD_EN.replace("Constraints: (none in the request or repo docs)",
                                   "Constraints: AGENTS.md — (the rule written in that doc)"))
        self.assertTrue(any("자리표시자" in e for e in r["errors"]), r["errors"])

    def test_rules(self):
        r = self.r(GOOD_EN.replace(ph.WORK_RULES_EN[2], "Run the tests."))
        self.assertTrue(any("'영어 요청일 때'" in e for e in r["errors"]), r["errors"])
        # 한국어 작업 규칙 한 벌이 온전하면 영어 프롬프트에서도 받아 준다 (내용이 같으므로)
        ko_rules = "Work rules:\n" + "".join(f"  - {x}\n" for x in ph.WORK_RULES)
        self.assertEqual(self.r(GOOD_EN.replace(RULES_EN, ko_rules))["errors"], [])
        # 작업 규칙의 "Commit, push…"는 위험 작업 표현으로 치지 않는다
        self.assertFalse(any("위험 작업" in w for w in self.r(GOOD_EN)["warnings"]))

    def test_symptom_words_are_not_terms(self):
        r = self.r(GOOD_EN.replace("closes the app", "shows undefined as the title"))
        self.assertFalse(any("개발 용어" in w for w in r["warnings"]), r["warnings"])
        r = self.crit("Login is handled properly — check: manual — log in")
        self.assertEqual(sum("properly" in w for w in r["warnings"]), 1, r["warnings"])

    def test_situation_code_and_terms(self):
        r = self.r(GOOD_EN.replace("closes the app", "makes the onClick handler throw and the API call fail"))
        self.assertTrue(any("코드 표기(onClick" in w for w in r["warnings"]), r["warnings"])
        self.assertTrue(any("개발 용어(API" in w for w in r["warnings"]), r["warnings"])
        r = self.r(GOOD_EN.replace("Goal: Fix", "Goal: Send the error to the server and fix"))
        self.assertTrue(any("'server'" in w for w in r["warnings"]), r["warnings"])

    def test_placeholders(self):
        r = self.crit("(what the user will see) — check: (how this criterion is checked)")
        self.assertTrue(any("자리표시자" in e for e in r["errors"]), r["errors"])
        r = self.r(GOOD_EN.replace("Goal: Fix the app closing when the password is wrong", "Goal: (what to do, in one sentence)"))
        self.assertTrue(any("'Goal:' 칸이 비었거나" in e for e in r["errors"]), r["errors"])

    def test_split_prompts(self):
        both = "Prompt 1/2\n" + GOOD_EN + "\n## Prompt 2/2 — settings\n" + GOOD_EN
        self.assertEqual(len(pl.split_prompts(both)), 2)
        self.assertEqual(len(pl.split_prompts(GOOD_EN + "\n" + GOOD_EN)), 2)

    def test_closing_line(self):
        self.assertIn("Here's what I understood", pl.to_text(pl.lint(GOOD_EN, self.root)))
        self.assertIn("이렇게 이해했습니다", pl.to_text(pl.lint(GOOD, self.root)))
        bad = pl.to_text(pl.lint(GOOD_EN.replace("Verify: ./gradlew test\n", ""), self.root))
        self.assertIn("Couldn't verify", bad)


class QuestionEnglish(unittest.TestCase):
    def test_question_checks(self):
        self.assertEqual(pl.check_question("Which screen looks wrong? (e.g. login / search)"), [])
        w = pl.check_question("Which API endpoint fails? What component? Which function?")
        self.assertTrue(any("질문이 3개" in x for x in w))
        self.assertTrue(any("API" in x and "component" in x and "function" in x for x in w), w)
        self.assertTrue(any("보기가 없습니다" in x for x in w))
        self.assertEqual(pl.check_question("Where does it happen? (for example: the search screen)"), [])

    def test_last_line_language(self):
        self.assertIn("'Quick question first'", pl.question_to_text([], pl.text_lang("Which screen?")))
        self.assertIn("'먼저 여쭤볼게요'", pl.question_to_text([], pl.text_lang("어느 화면인가요?")))
        p = subprocess.run([sys.executable, str(SKILL_DIR / "prompt_lint.py"), "--question"],
                           input="Which screen? (e.g. login / search)", capture_output=True, text=True,
                           timeout=30, env=ENV)
        self.assertIn("Quick question first", p.stdout)


class SkillContractEnglish(unittest.TestCase):
    def test_template_and_rules_in_skill(self):
        self.assertIn(ph.TEMPLATE_EN, SKILL_MD)
        self.assertEqual(len(ph.WORK_RULES_EN), 6)
        for b in en_examples():
            for rule in ph.WORK_RULES_EN:
                self.assertIn("  - " + rule, b)

    def test_english_example_passes_lint(self):
        blocks = en_examples()
        self.assertEqual(len(blocks), 1)
        d, root = make_repo()
        with d:
            r = result(blocks[0], root)
            self.assertEqual((r["errors"], r["warnings"], r["lang"]), ([], [], "en"))

    def test_phrases_documented(self):
        for lang in ("ko", "en"):
            for phrase in pl.PHRASES[lang].values():
                self.assertIn(phrase, SKILL_MD)
        self.assertIn("영어 요청일 때", SKILL_MD)
        for mark in ("(new file)", "(needs check)", "— check:", "(source:"):
            self.assertIn(mark, SKILL_MD)


if __name__ == "__main__":
    unittest.main()
