"""답 전체 점검과 되묻기 안전장치 테스트 — 평가(Opus 4.6, 쇼핑몰 저장소)에서 실제로 나온 실패 모양으로 확인한다.

  - h7 "배포해줘": 질문 보기가 Vercel / Netlify / AWS (사용자가 고를 수 없음)
  - h6·e7: '제가 정한 것'·'넣지 않은 것'에 `cartTotal` 같은 코드 이름
  - h7: '제가 정한 것'에 "정적 파일(HTML, JS, CSS)을 호스팅"
  - 가리키는 말·끝이 없는 표현이 있는데 묻지도 않고 가정도 없는 프롬프트

실행: python3 -m unittest discover -s tests
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_prompt_lint import ENV, GOOD, SKILL_DIR, SKILL_MD, make_repo  # noqa: E402
from test_english import GOOD_EN  # noqa: E402

import prompt_lint as pl  # noqa: E402

CLOSE_KO = pl.CLOSING["ko"]
CLOSE_EN = pl.CLOSING["en"]


def answer(prompt, before="**이렇게 이해했습니다**\n\n로그인 화면에서 비밀번호를 틀리면 앱이 꺼집니다.\n\n**프롬프트**\n",
           after="\n" + CLOSE_KO):
    return before + "```\n" + prompt + "```\n" + after


class AnswerCheck(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def aw(self, text):
        r = pl.lint(text, self.root)
        self.assertEqual(len(r["prompts"]), 1)
        return r["answer_warnings"]

    def test_clean_answer(self):
        self.assertEqual(self.aw(answer(GOOD)), [])
        en = answer(GOOD_EN, before="**Here's what I understood**\n\nThe app closes when the password is wrong.\n\n", after="\n" + CLOSE_EN)
        self.assertEqual(self.aw(en), [])

    def test_prompt_only_has_no_answer_check(self):
        r = pl.lint(GOOD, self.root)
        self.assertEqual(r["answer_warnings"], [])
        # 프롬프트를 코드 블록에만 넣고 다른 말이 없어도 답 점검은 하지 않는다
        self.assertEqual(pl.lint("```\n" + GOOD + "```\n", self.root)["answer_warnings"], [])

    def test_prompt_inside_block_is_linted_alone(self):
        # 블록 밖의 말이 마지막 칸(작업 규칙)에 이어 붙지 않는다
        r = pl.lint(answer(GOOD, after="\n**넣지 않은 것**\n\n- 배포 설정도 찾았습니다.\n\n" + CLOSE_KO), self.root)
        self.assertEqual(r["prompts"][0]["errors"], [])
        self.assertNotIn("넣지 않은 것", r["prompts"][0]["fields"]["작업 규칙"])

    def test_first_and_last_line(self):
        w = self.aw(answer(GOOD, before="점검을 통과했습니다.\n\n**이렇게 이해했습니다**\n\n앱이 꺼집니다.\n\n"))
        self.assertTrue(any("첫 줄" in x for x in w), w)
        self.assertTrue(any("점검 과정" in x for x in w), w)
        w = self.aw(answer(GOOD, after="\n이대로 진행할까요?"))
        self.assertTrue(any("마지막 줄" in x for x in w), w)

    def test_code_names_outside_prompt(self):
        # h6·e7: 사용자에게 보이는 말에 코드 이름
        w = self.aw(answer(GOOD, after="\n**넣지 않은 것**\n\n- 장바구니 합계(`cartTotal`)가 수량을 곱하지 않습니다.\n\n" + CLOSE_KO))
        self.assertTrue(any("cartTotal" in x for x in w), w)
        w = self.aw(answer(GOOD, after="\n**제가 정한 것**\n\n- onSubmit에서 setError를 부르는 것으로 정했습니다.\n\n" + CLOSE_KO))
        self.assertTrue(any("코드 표기" in x for x in w), w)

    def test_dev_terms_outside_prompt(self):
        # h7: '제가 정한 것'에 기술 용어
        w = self.aw(answer(GOOD, before="**이렇게 이해했습니다**\n\n사이트를 올립니다.\n\n**제가 정한 것**\n\n"
                                        "- 빌드한 정적 파일(HTML, JS, CSS)을 호스팅 서비스에 올리는 것으로 봤습니다.\n\n"))
        self.assertTrue(any("개발 용어" in x and "HTML" in x and "호스팅" in x for x in w), w)

    def test_user_pasted_text_in_backticks_is_ok(self):
        # e8: 사용자가 붙여 넣은 에러 문구를 인용한 것은 사용자의 말이다
        req = "TypeError: Cannot read properties of undefined (reading 'token') 이거 뭐야"
        prompt = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: " + req)
        text = answer(prompt, before="**이렇게 이해했습니다**\n\n로그인하면 `TypeError: Cannot read properties of undefined "
                                     "(reading 'token')` 에러가 납니다.\n\n")
        w = self.aw(text)
        self.assertFalse(any("코드 표기" in x for x in w), w)

    def test_cli_prints_answer_section(self):
        text = answer(GOOD, after="\n- `cartTotal`도 이상합니다.\n\n" + CLOSE_KO)
        p = subprocess.run([sys.executable, str(SKILL_DIR / "prompt_lint.py"), "--root", str(self.root)], input=text,
                           capture_output=True, text=True, timeout=30, env=ENV)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("답(프롬프트 밖의 말)", p.stdout)
        self.assertIn("cartTotal", p.stdout)


class CueGuard(unittest.TestCase):
    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def w(self, prompt):
        return pl.lint(prompt, self.root)["prompts"][0]["warnings"]

    def test_pointer_without_question_or_assumption(self):
        p = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: 이거 버그났어") \
                .replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)", "가정: 없음")
        self.assertTrue(any("되묻지도 않았고 가정도 없습니다" in x for x in self.w(p)))
        # 가정에 무엇으로 봤는지 적었으면 괜찮다
        p2 = p.replace("가정: 없음", "가정:\n  - '이거'를 로그인 화면에서 앱이 꺼지는 문제로 봄")
        self.assertFalse(any("되묻지도" in x for x in self.w(p2)))
        # 되물어 받은 답이 붙어 있으면 괜찮다
        p3 = p.replace("요청 원문: 이거 버그났어", "요청 원문: 이거 버그났어 / 로그인이요. 비번 틀리면 꺼져요")
        self.assertFalse(any("되묻지도" in x for x in self.w(p3)))

    def test_open_ended_without_question(self):
        p = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: 화면 좀 예쁘게 해줘") \
                .replace("가정: 재현 테스트 추가 여부는 요청에 없음 (확인 필요)", "가정: 없음")
        self.assertTrue(any("예쁘게" in x for x in self.w(p)))

    def test_new_feature_without_question(self):
        p = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: 결제 붙여줘")
        self.assertTrue(any("새로 만드는 요청" in x for x in self.w(p)))
        p2 = GOOD.replace("요청 원문: 비밀번호 틀리면 앱이 꺼져", "요청 원문: 결제 붙여줘 / 버튼 누르면 결제됐다고 나오면 돼요")
        self.assertFalse(any("새로 만드는 요청" in x for x in self.w(p2)))

    def test_specific_request_is_quiet(self):
        self.assertEqual(self.w(GOOD), [])

    def test_english(self):
        p = GOOD_EN.replace("Original request: the app closes when the password is wrong", "Original request: this is broken")
        self.assertTrue(any("되묻지도" in x for x in self.w(p)))
        p = GOOD_EN.replace("Original request: the app closes when the password is wrong", "Original request: add a share button")
        self.assertTrue(any("새로 만드는 요청" in x for x in self.w(p)))


class Round2Fixes(unittest.TestCase):
    """2차 평가에서 나온 실패: h4 '파일을 열어' 확인, h7 상황의 '빌드·호스팅', n3 답의 'router·server'"""

    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def test_manual_check_opening_files(self):
        p = GOOD.replace("확인: 기존 테스트 app/src/test/java/com/example/auth/LoginViewModelTest.kt",
                         "확인: 사람이 직접 — 파일을 열어 에러 처리 코드가 있는지 본다")
        w = pl.lint(p, self.root)["prompts"][0]["warnings"]
        self.assertTrue(any("개발 도구" in x for x in w), w)

    def test_service_terms_in_situation(self):
        p = GOOD.replace("상황: 로그인 화면에서", "상황: 빌드해서 호스팅 서비스에 올리려 한다. 로그인 화면에서")
        w = pl.lint(p, self.root)["prompts"][0]["warnings"]
        self.assertTrue(any("개발 용어" in x and "빌드" in x for x in w), w)

    def test_server_and_router_in_answer(self):
        text = answer(GOOD_EN, before="**Here's what I understood**\n\nThe app closes.\n\n**What I decided**\n\n"
                                       "- It shows on the same page since the app has no router, and won't talk to a server.\n\n",
                      after="\n" + CLOSE_EN)
        w = pl.lint(text, self.root)["answer_warnings"]
        self.assertTrue(any("router" in x and "server" in x for x in w), w)

    def test_closing_line_forbids_fix_talk(self):
        self.assertIn("…하겠습니다", pl.to_text(pl.lint(GOOD, self.root)))
        self.assertIn("고친 이야기", pl.question_to_text(["x"]))


class PressOutcome(unittest.TestCase):
    """새 요청 k2 "결제 버튼 회색임": 누를 수 있게만 하고 누른 뒤 결과가 없는 프롬프트"""

    def setUp(self):
        self.d, self.root = make_repo()

    def tearDown(self):
        self.d.cleanup()

    def w(self, line):
        p = GOOD.replace("  - 잘못된 비밀번호로 로그인해도 앱이 종료되지 않는다 — 확인: 기존 테스트 "
                         "app/src/test/java/com/example/auth/LoginViewModelTest.kt", "  - " + line)
        return pl.lint(p, self.root)["prompts"][0]["warnings"]

    def test_can_press_only(self):
        w = self.w("장바구니에 상품이 있으면 결제하기 버튼이 활성화되어 누를 수 있다 — 확인: 사람이 직접 — 버튼을 눌러 본다")
        self.assertTrue(any("누른 뒤" in x for x in w), w)
        self.assertTrue(pl.check_press_outcome(["The checkout button is enabled and clickable"]))

    def test_with_outcome(self):
        w = self.w("결제하기 버튼을 누르면 \"결제가 완료되었습니다\"가 보인다 — 확인: 사람이 직접 — 버튼을 눌러 본다")
        self.assertFalse(any("누른 뒤" in x for x in w), w)
        self.assertEqual(pl.check_press_outcome(["The button is clickable", "Pressing the button shows a confirmation"]), [])
        self.assertEqual(pl.check_press_outcome(["검색 결과가 나온다"]), [])


class GraderFindings(unittest.TestCase):
    """독립 채점에서 나온 지적: e6·k3 속도 기준, e8 typecheck로 동작 확인, n1·n4·k6 번역 없는 한국어 제약"""

    def test_speed_manual_only(self):
        w = pl.check_check_methods([("상품 목록이 지금보다 빨리 뜬다", "사람이 직접 — 열어 보고 견주어 본다")])
        self.assertTrue(any("속도 기준" in x for x in w), w)
        w = pl.check_check_methods([("상품 목록이 지금보다 빨리 뜬다", "사람이 직접 — 열어 본다"),
                                    ("상품을 한꺼번에 불러온다", "이 경우를 검사하는 테스트를 추가해 pnpm test로")])
        self.assertFalse(any("속도 기준" in x for x in w), w)
        w = pl.check_check_methods([("상품 목록이 빨리 뜬다", "이 경우를 검사하는 테스트를 추가해 pnpm test로")])
        self.assertEqual(w, [])

    def test_static_check_for_behavior(self):
        w = pl.check_check_methods([("TypeError가 나지 않는다", "pnpm typecheck")])
        self.assertTrue(any("타입 검사" in x for x in w), w)
        self.assertEqual(pl.check_check_methods([("typecheck와 lint가 통과한다", "pnpm typecheck && pnpm lint")]), [])
        self.assertEqual(pl.check_check_methods([("안내가 보인다", "기존 테스트 tests/test_a.py")]), [])

    def test_korean_constraint_in_english_prompt(self):
        self.assertTrue(pl.check_en_constraints("AGENTS.md — 새 의존성을 추가하지 않는다", "en"))
        self.assertEqual(pl.check_en_constraints("AGENTS.md — 새 의존성을 추가하지 않는다 (no new dependencies)", "en"), [])
        self.assertEqual(pl.check_en_constraints("AGENTS.md — 새 의존성을 추가하지 않는다", "ko"), [])
        self.assertEqual(pl.check_en_constraints("request — keep the old flow", "en"), [])


class QuestionServiceNames(unittest.TestCase):
    def test_hosting_choices(self):
        # h7: 사용자가 고를 수 없는 보기
        w = pl.check_question("어디에 배포하면 될까요?\n- Vercel\n- Netlify\n- AWS (S3 + CloudFront)\n- 다른 곳 (알려 주세요)")
        self.assertTrue(any("Vercel" in x and "AWS" in x for x in w), w)
        self.assertEqual(pl.check_question("어느 화면인가요? (예: 로그인 / 장바구니)"), [])


class SkillContractAnswers(unittest.TestCase):
    def full_answers(self):
        out = []
        for start, end in [("**이렇게 이해했습니다**\n\n메모를 검색", "### 예시 3"),
                           ("**이렇게 이해했습니다**\n\n메모 쓰기", None),
                           ("**Here's what I understood**", "## 사용자가 실행을")]:
            i = SKILL_MD.index(start)
            j = SKILL_MD.index(end, i) if end else len(SKILL_MD)
            out.append(SKILL_MD[i:j])
        return out

    def test_skill_full_answers_pass(self):
        # SKILL.md 예시 답 전체(프롬프트 밖의 말 포함)가 점검을 ✗·△ 없이 통과해야 모델이 따라 해도 안전하다
        d, root = make_repo()
        with d:
            for text in self.full_answers():
                with self.subTest(text=text[:40]):
                    r = pl.lint(text, root)
                    self.assertEqual(len(r["prompts"]), 1)
                    self.assertEqual((r["prompts"][0]["errors"], r["prompts"][0]["warnings"], r["answer_warnings"]),
                                     ([], [], []))

    def test_skill_says_lint_whole_answer(self):
        self.assertIn("답 전체", SKILL_MD)
        self.assertIn("서비스·도구 이름", SKILL_MD)
        bash = [b for b in re.findall(r"```bash\n(.*?)```", SKILL_MD, re.S) if "prompt_lint.py" in b and "--question" not in b]
        self.assertEqual(len(bash), 1)
        self.assertIn("(보여 줄 답 전체를 여기에 그대로", bash[0])


if __name__ == "__main__":
    unittest.main()
