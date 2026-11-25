"""독립 코드 리뷰(2026-11-25)에서 찾은 실패 입력을 그대로 고정한다.

H1 아포스트로피가 든 인용 문구의 거짓 ✗, H2 영어 STRONG 패턴의 거짓 ✗, H3 영어 에러 문구를 붙인 한국어 요청이 영어로 분류,
H4 한국어 프롬프트 안의 영어 같은 줄로 프롬프트가 쪼개짐, M5~M9·L10~L15

실행: python3 -m unittest discover -s tests
"""

import re
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_prompt_lint import GOOD, make_repo  # noqa: E402
from test_english import GOOD_EN  # noqa: E402
from test_answer_guards import CLOSE_KO, answer  # noqa: E402

import prompt_helper as ph  # noqa: E402
import prompt_lint as pl  # noqa: E402

KO_LINE = ("  - 잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다 — 확인: 기존 테스트 "
           "app/src/test/java/com/example/auth/LoginViewModelTest.kt")
EN_LINE = ("  - Entering a wrong password keeps the app open — check: existing test "
           "app/src/test/java/com/example/auth/LoginViewModelTest.kt")


class Base(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def one(self, prompt):
        r = pl.lint(prompt, self.root)
        return r

    def en_crit(self, line):
        return self.one(GOOD_EN.replace(EN_LINE, "  - " + line))["prompts"][0]


class H1Apostrophes(Base):
    def test_quoted_text_with_apostrophe(self):
        for line in ["The banner shows 'Everything's working fine' — check: manual — open the app",
                     "The dialog shows 'Don't worry, it works fine' — check: manual — open"]:
            with self.subTest(line=line):
                r = self.en_crit(line)
                self.assertEqual(r["errors"], [], r["errors"])

    def test_helper_quotes_with_apostrophe(self):
        self.assertEqual(ph.analyze_request("the page shows 'Something's wrong here' after login")["pointers"], [])


class H2EnglishStrong(Base):
    def test_observable_sentences_are_not_errors(self):
        for line in ["The search function correctly ignores capitalization — check: add a test for this case and run ./gradlew test",
                     "The issues tab shows no issues for a new project — check: manual — open a new project",
                     "The sync job runs normally at night and the badge shows 'Synced' — check: manual — wait and look"]:
            with self.subTest(line=line):
                self.assertEqual(self.en_crit(line)["errors"], [])

    def test_clear_cases_still_errors(self):
        self.assertTrue(self.en_crit("Login works correctly — check: manual — log in")["errors"])
        self.assertTrue(self.en_crit("Login finishes without any issues — check: manual — log in")["errors"])


class H3RequestLanguage(unittest.TestCase):
    def test_pasted_english_errors_in_korean_requests(self):
        for t in ["TypeError: Cannot read properties of undefined (reading 'token') 이거 뭐야",
                  "Something went wrong 떠요", "Failed to fetch 에러 나요",
                  "npm run build 하면 Module not found: Can't resolve 'react' 나와요"]:
            with self.subTest(t=t):
                r = ph.analyze_request(t)
                self.assertEqual(r["lang"], "ko")
                # 한국어 가리키는 말("이거")은 잡아도 되지만, 붙여 넣은 영어 문구에서 영어 단서를 뽑지 않는다
                self.assertFalse([x for x in r["pointers"] + r["open_ended"] + r["new_feature"] if x.isascii()], t)

    def test_english_requests(self):
        for t in ["this is broken", "fix the cart total on the 장바구니 page please, it shows the wrong sum"]:
            with self.subTest(t=t):
                self.assertEqual(ph.request_lang(t), "en")


class H4KoreanPromptNotSplit(Base):
    def test_english_looking_lines_in_korean_prompt(self):
        for extra_at, text in [("end", "참고: 로그에 아래가 찍힌다\nRequest: POST /api/login\nResponse: 500\n"),
                               ("assume", None)]:
            with self.subTest(case=extra_at):
                if text:
                    p = GOOD.replace("참고: app/src/test/java/com/example/auth/LoginViewModelTest.kt\n", text)
                else:
                    p = GOOD.replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)", "가정:\n- Goal: 앱이 꺼지지 않게만 한다")
                r = self.one(p)
                self.assertEqual(len(r["prompts"]), 1)
                self.assertEqual(r["prompts"][0]["errors"], [])

    def test_english_variants_still_work_in_english_prompt(self):
        r = self.one(GOOD_EN.replace("Done when:", "Acceptance criteria:"))
        self.assertEqual(r["prompts"][0]["errors"], [])
        self.assertEqual(r["prompts"][0]["lang"], "en")


class Medium(Base):
    def test_m5_log_block_is_not_prompt(self):
        text = ("**이렇게 이해했습니다**\n\n앱이 꺼집니다.\n\n```\nTarget: ES2020\nerror TS2304\n```\n\n"
                "**프롬프트**\n```\n" + GOOD + "```\n" + CLOSE_KO)
        r = self.one(text)
        self.assertEqual(len(r["prompts"]), 1)
        self.assertEqual(r["prompts"][0]["errors"], [])

    def test_m6_prompt_numbers_and_and_or(self):
        text = ("**이렇게 이해했습니다**\n\n앱이 꺼집니다.\n\n**프롬프트 1/2**\n```\n" + GOOD + "```\n**프롬프트 2/2**\n```\n"
                + GOOD + "```\n" + CLOSE_KO)
        r = self.one(text)
        self.assertEqual(len(r["prompts"]), 2)
        self.assertEqual(r["answer_warnings"], [])

    def test_m7_users_own_words(self):
        req = "2단계 인증 화면에서 배포 스크립트를 돌리면 빌드할 때 에러가 나요"
        p = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: " + req) \
                .replace("상황: 로그인 화면에서", "상황: 앱을 빌드할 때 에러가 난다. 로그인 화면에서")
        text = answer(p, before="**이렇게 이해했습니다**\n\n2단계 인증 화면에서 배포 스크립트를 돌리면 에러가 납니다.\n\n")
        r = self.one(text)
        self.assertFalse(any("개발 용어" in w for w in r["prompts"][0]["warnings"]), r["prompts"][0]["warnings"])
        self.assertEqual(r["answer_warnings"], [])
        self.assertEqual(pl.check_answer("Here's what I understood\n\nThe helper text under the field is cut off.\n\n"
                                         + pl.CLOSING["en"], "en"), [])

    def test_m8_settings_are_not_buttons(self):
        self.assertEqual(pl.check_press_outcome(["설정에서 다크 모드를 켜면 다크 모드가 활성화되어 화면이 어두워진다"]), [])
        self.assertEqual(pl.check_press_outcome(["When the setting is enabled, the list shows dates"]), [])
        self.assertTrue(pl.check_press_outcome(["결제하기 버튼이 활성화되어 누를 수 있다"]))

    def test_m9_user_opens_downloaded_file(self):
        self.assertIsNone(pl.DEV_TOOL_RE.search("사람이 직접 — 내려받은 파일을 열어 행 수를 본다"))
        self.assertTrue(pl.DEV_TOOL_RE.search("사람이 직접 — 파일을 열어 error 관련 코드가 남아 있지 않은지 본다"))
        self.assertTrue(pl.DEV_TOOL_RE.search("사람이 직접 — 소스 코드를 열어 본다"))


class LangByPredicate(unittest.TestCase):
    """재검토 N-1: 영어 요청에 한국어 화면 이름을 섞으면 한국어로 분류되던 문제"""

    def test_english_with_korean_names(self):
        for t in ["the 장바구니 page and the 결제 button are broken", "fix the login bug please, the error says 로그인 실패",
                  "로그인 is broken"]:
            with self.subTest(t=t):
                self.assertEqual(ph.request_lang(t), "en")

    def test_korean_endings(self):
        for t in ["로그인 버그 고쳐", "이거 좀 이상함", "결제 버튼 회색임", "Login.kt에서 push해줘", "검색이 안 돼", "너무 느려",
                  "버그 수정 좀", "TypeError: Cannot read properties of undefined (reading 'token') 이거 뭐야"]:
            with self.subTest(t=t):
                self.assertEqual(ph.request_lang(t), "ko")


class LintReminder(Base):
    """짐작한 증상은 낱말로 가려낼 수 없어(문장·낱말 단위 실험 모두 실패) 고칠 순간의 안내로 둔다"""

    def test_reminder_in_output(self):
        out = pl.to_text(pl.lint(GOOD, self.root))
        self.assertIn("코드를 읽고 짐작한 증상", out)
        self.assertFalse(hasattr(pl, "check_guessed_symptoms"))


class DangerFields(Base):
    """평가 163회에서 위험 작업 △ 11번이 거의 제약·대상의 소음이었다 — 무엇을 할지 말하는 칸만 본다"""

    def danger(self, prompt):
        return [w for w in self.one(prompt)["prompts"][0]["warnings"] if "위험 작업" in w]

    def test_repo_rules_and_names_are_quiet(self):
        p = GOOD.replace("제약: (요청·저장소 문서에 없음)", "제약: AGENTS.md — 문서와 커밋 메시지는 한국어로 쓴다") \
                .replace("참고: app/", "참고: 삭제 버튼 옆 app/")
        self.assertEqual(self.danger(p), [])

    def test_goal_still_checked(self):
        p = GOOD.replace("목표: 비밀번호가 틀렸을 때 앱이 종료되는 버그를 고친다", "목표: 고친 뒤 운영 서버에 배포한다")
        self.assertTrue(self.danger(p))


class ScriptBodyCommand(unittest.TestCase):
    """할 일 앱 평가 g6: 검증에 package.json 스크립트 본문('vitest run')을 그대로 씀 — 실행마다 명령이 갈렸다"""

    def test_body_instead_of_name(self):
        import json as _json
        d, root = make_repo({"package.json": _json.dumps({"scripts": {"test": "vitest run", "lint": "eslint ."}}),
                             "package-lock.json": "{}", "src/a.ts": ""})
        with d:
            w = pl.check_verify("vitest run (출처: package.json)", "src/a.ts", root)[1]
            self.assertTrue(any("'npm test'" in x for x in w), w)
            w = pl.check_verify("eslint .", "src/a.ts", root)[1]
            self.assertTrue(any("'npm run lint'" in x for x in w), w)
            self.assertEqual(pl.check_verify("npm test", "src/a.ts", root)[1], [])


class NoEvalContamination(unittest.TestCase):
    """평가 저장소(쇼핑몰, 파이썬 서버, 할 일 앱)의 내용을 스킬 규칙의 예시로 쓰지 않는다 — 시험 문제의 답을 교재에 적는 셈이다.
    2026-11-25에 SKILL.md 예시에 평가 사례 문장("할 일 20개를 동기화해도…")을 넣었다가 회귀 확인이 그 문장을 베낀 일이 있었다"""

    EVAL_WORDS = ["장바구니", "상품", "결제", "동기화", "할 일 목록", "다크 모드", "마감", "주문 내역",
                  "cartTotal", "formatDate", "filterTodos", "daysLeft", "syncAll", "shop/", "src/cart", "src/todos"]

    def test_skill_and_lint_messages_have_no_eval_content(self):
        from test_prompt_lint import SKILL_MD
        lint_src = (Path(pl.__file__)).read_text(encoding="utf-8")
        # 코드 주석은 모델에게 보이지 않으므로 뺀다
        lines = [l for l in lint_src.splitlines() if not l.lstrip().startswith("#")]
        lint_msgs = "\n".join(re.sub(r"\s{2,}#\s.*$", "", l) for l in lines)     # 줄 끝 주석("코드  # 설명")도 뺀다
        for w in self.EVAL_WORDS:
            with self.subTest(word=w):
                self.assertNotIn(w, SKILL_MD)
                self.assertNotIn(w, lint_msgs)


class Low(Base):
    def test_l10_first_and_last_line_forms(self):
        en = ("Here’s what I understood\n\nThe app closes.\n\n" + pl.CLOSING["en"])
        self.assertEqual(pl.check_answer(en, "en"), [])
        ko = "**이렇게 이해했습니다** — 로그인 화면에서 앱이 꺼집니다.\n\n**\"" + CLOSE_KO + "\"**"
        self.assertEqual(pl.check_answer(ko, "ko"), [])

    def test_l12_none_prefix(self):
        self.assertFalse(pl.NONE_MARK.match("none of the existing login screens may change layout"))
        self.assertTrue(pl.NONE_MARK.match("none"))
        self.assertTrue(pl.NONE_MARK.match("(none in the request or repo docs)"))
        self.assertTrue(pl.NONE_MARK.match("(요청·저장소 문서에 없음)"))
        self.assertEqual(pl._count_assumptions("None of the screens change"), 1)

    def test_l13_user_facing_is_not_source(self):
        self.assertFalse(pl.SOURCE_MARK.search("keep the user-facing text unchanged"))
        self.assertTrue(pl.SOURCE_MARK.search("request — keep the old flow"))
        self.assertTrue(pl.SOURCE_MARK.search("AGENTS.md — 새 의존성을 추가하지 않는다"))

    def test_l15_single_words_are_not_fields(self):
        self.assertNotIn("완료 기준", ph.analyze_request("it crashes until I restart")["present_fields"])
        self.assertNotIn("제약", ph.analyze_request("never mind the header, fix the footer")["present_fields"])
        self.assertIn("제약", ph.analyze_request("fix the footer but don't change the header")["present_fields"])


if __name__ == "__main__":
    unittest.main()
