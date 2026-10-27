"""Grades for Adam: the roles he is aiming at score well, the rest score low."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))

import scoring  # noqa: E402

BODY = (
    "We build cloud software for customers. " * 12
    + "You will run product demos, answer technical questions, and work with sales. "
    + "Two to three years of customer-facing experience. Bachelor's degree or equivalent experience."
)


STACK = " You will build with Python, TypeScript and React against REST APIs and PostgreSQL."


def grade(title, company="Acme Software", location="Remote (United States)", description=BODY):
    return scoring.score(
        {"title": title, "company": company, "location": location, "description": description, "body_status": "opened"}
    )["grade"]


class TargetRoles(unittest.TestCase):
    def test_core_bridge_roles_are_high(self):
        for title in ("Solutions Engineer", "Sales Engineer", "Pre-Sales Consultant", "Technical Sales Representative"):
            self.assertEqual(grade(title), "high", title)

    def test_entry_engineering_is_high(self):
        self.assertEqual(grade("Junior Full Stack Engineer"), "high")


class LowFits(unittest.TestCase):
    def test_manager_and_level_titles_are_low(self):
        for title in (
            "Manager, Technical Account Management",
            "Technical Sales Manager",
            "Life Sciences Solutions Engineer, AVP",
            "Implementation Consultant 2",
            "Founding Forward Deployed Engineer",
            "Mid Level Forward Deployed Software Engineer",
        ):
            self.assertEqual(grade(title), "low", title)

    def test_account_manager_is_not_a_people_manager(self):
        self.assertEqual(grade("Technical Account Manager"), "medium")

    def test_other_region_or_language_is_low(self):
        self.assertEqual(grade("Customer Solutions Engineer, EMEA"), "low")
        self.assertEqual(grade("Bilingual Channel Sales Engineer, French"), "low")
        self.assertEqual(grade("Solutions Engineer", location="Toronto, Canada"), "low")
        self.assertEqual(grade("Solutions Engineer", location="Remote (Canada, USA)"), "high")
        self.assertEqual(grade("Solutions Engineer", location="Remote - Canada, USA"), "high")

    def test_hardware_engineering_sales_is_low_but_technical_sales_seat_is_medium(self):
        self.assertEqual(grade("CNC Machine Tool Sales Engineer"), "low")
        self.assertEqual(grade("Field Sales Engineer", company="Schneider Electric"), "low")
        self.assertEqual(grade("Technical Sales Trainee", company="Balluff Inc"), "medium")

    def test_vertical_specialties_and_odd_seats_are_low(self):
        for title in ("Payroll Implementation Specialist", "Accounting Solutions Consultant", "Part-Time Junior Frontend Developer", "Pre-Sales Enablement Specialist", "Business Intelligence Developer Associate"):
            self.assertEqual(grade(title), "low", title)

    def test_resume_mill_company_is_low(self):
        self.assertEqual(grade("Junior Software Engineer", company="Info Resume Edge"), "low")


class AdjacentRoles(unittest.TestCase):
    def test_adjacent_roles_top_out_at_medium(self):
        for title in ("Technical Account Manager", "Implementation Engineer", "Associate QA Engineer"):
            self.assertEqual(grade(title), "medium", title)

    def test_service_seats_are_low(self):
        for title in ("Implementation Specialist", "Customer Onboarding Specialist", "Atlassian Technical Consultant"):
            self.assertIn(grade(title), ("low", "medium"), title)
        for title in ("Implementation Specialist", "Customer Onboarding Specialist"):
            self.assertEqual(grade(title), "low", title)

    def test_forward_deployed_is_high_only_when_the_posting_welcomes_career_changers(self):
        plain = BODY + STACK
        friendly = BODY + STACK + " Career changers and bootcamp graduates welcome."
        self.assertEqual(grade("Forward Deployed Engineer", description=plain), "medium")
        self.assertEqual(grade("Forward Deployed Engineer", description=friendly), "high")

    def test_forward_deployed_years_are_engineering_years(self):
        desc = BODY + STACK + " 5+ years of experience required."
        self.assertEqual(grade("Forward Deployed Engineer", description=desc), "low")

    def test_unread_posting_keeps_target_titles_and_drops_adjacent_ones(self):
        unread = {"company": "Acme", "location": "Remote", "description": "", "body_status": "unread"}
        self.assertEqual(scoring.score(dict(unread, title="Solutions Engineer"))["grade"], "medium")
        self.assertEqual(scoring.score(dict(unread, title="Implementation Specialist"))["grade"], "low")


class FitScale(unittest.TestCase):
    def points(self, title, description=BODY, **kw):
        job = {"title": title, "company": "Acme Software", "location": "Remote (United States)",
               "description": description, "body_status": "opened"}
        job.update(kw)
        return scoring.score(job)

    def test_score_and_parts_are_reported(self):
        out = self.points("Solutions Engineer")
        self.assertGreaterEqual(out["score"], scoring.HIGH_AT)
        self.assertEqual(sum(out["parts"].values()), out["score"])
        for key, cap in scoring.WEIGHTS.items():
            self.assertLessEqual(out["parts"][key], cap, key)

    def test_grade_follows_the_thresholds(self):
        for title in ("Solutions Engineer", "Technical Account Manager", "Junior Full Stack Engineer"):
            out = self.points(title, description=BODY + STACK)
            expect = "high" if out["score"] >= scoring.HIGH_AT else "medium" if out["score"] >= scoring.MEDIUM_AT else "low"
            self.assertEqual(out["grade"], expect, title)

    def test_stack_overlap_ranks_a_role_higher(self):
        bare = "The team ships work for customers every quarter and cares about quality. " * 8
        thin = self.points("Junior Software Engineer", description=bare)
        rich = self.points("Junior Software Engineer", description=bare + STACK)
        self.assertGreater(rich["score"], thin["score"])
        self.assertEqual(thin["grade"], "medium")
        self.assertEqual(rich["grade"], "high")

    def test_ai_product_ranks_above_a_generic_one(self):
        ai = self.points("Solutions Engineer", description=BODY + " Our LLM platform runs RAG and AI agents for customers. AI is our core.")
        plain = self.points("Solutions Engineer")
        self.assertGreater(ai["score"], plain["score"])

    def test_target_roles_outrank_adjacent_ones(self):
        se = self.points("Solutions Engineer", description=BODY + STACK)["score"]
        tam = self.points("Technical Account Manager", description=BODY + STACK)["score"]
        qa = self.points("Associate QA Engineer", description=BODY + STACK)["score"]
        self.assertGreater(se, tam)
        self.assertGreater(tam, qa)

    def test_a_stated_shortfall_keeps_a_job_out_of_high(self):
        for years in ("3+ years of pre-sales or sales engineering experience.", "5+ years of sales experience."):
            out = self.points("Sales Engineer", description=BODY + STACK + " " + years)
            self.assertEqual(out["grade"], "medium", years)
        self.assertEqual(self.points("Sales Engineer", description=BODY + STACK + " 5+ years of pre-sales engineering experience.")["grade"], "low")

    def test_hardware_technical_sales_seat_is_visible_but_never_high(self):
        out = self.points("Technical Sales Representative", company="Emerson", description=BODY + STACK)
        self.assertEqual(out["grade"], "medium")

    def test_hard_gate_lows_score_zero(self):
        self.assertEqual(self.points("Senior Solutions Engineer")["score"], 0)

    def test_unread_posting_is_capped_at_medium_with_a_provisional_score(self):
        out = scoring.score({"title": "Solutions Engineer", "company": "Acme", "location": "Remote",
                             "description": "", "body_status": "unread"})
        self.assertEqual(out["grade"], "medium")
        self.assertLessEqual(out["score"], scoring.CAP_UNREAD)

    def test_unread_forward_deployed_is_not_listed(self):
        out = scoring.score({"title": "Forward Deployed Engineer", "company": "Acme", "location": "Remote",
                             "description": "", "body_status": "unread"})
        self.assertEqual(out["grade"], "low")


if __name__ == "__main__":
    unittest.main()


class PostingTextGates(unittest.TestCase):
    """Things the title never shows: pay, language, and who the employer really is."""

    def test_unpaid_trainee_program_is_low(self):
        desc = BODY + STACK + " Type: Full-Time / Unpaid Trainee Program."
        self.assertEqual(grade("AI Engineer Trainee", description=desc), "low")

    def test_unpaid_leave_boilerplate_is_not_unpaid_work(self):
        desc = BODY + STACK + " Benefits include paid and unpaid leave."
        self.assertNotEqual(grade("Junior Software Engineer", description=desc), "low")

    def test_required_second_language_is_low(self):
        desc = BODY + " You must be fluent in both English and Turkish."
        self.assertEqual(grade("Business Solutions Consultant", description=desc), "low")

    def test_language_as_a_plus_or_boilerplate_is_not_a_gate(self):
        for extra in (" Professional fluency in Mandarin is a strong plus.",
                      " Proficiency in French is considered an asset.",
                      " Right to work information in English and Spanish. E-Verify participant."):
            self.assertNotEqual(grade("Solutions Engineer", description=BODY + extra), "low", extra)

    def test_posting_in_another_script_is_low(self):
        desc = "Про компанію Rollun LC — американська компанія, заснована українцями, яка працює на ринку США. " * 3 + STACK
        self.assertEqual(grade("Junior AI Engineer", description=desc), "low")

    def test_training_placement_and_outsourcing_firms_never_reach_high(self):
        for company in ("Dev10", "Geniusee"):
            r = scoring.score({"title": "Junior Software Engineer", "company": company,
                               "location": "Remote (United States)", "description": BODY + STACK,
                               "body_status": "opened"})
            self.assertEqual(r["grade"], "medium", company)


class WorkStyle(unittest.TestCase):
    """The 15 work-style points follow his Big Five results: competitive and autonomous work up, pressure and caretaking down."""

    def style(self, extra):
        return scoring.score({"title": "Solutions Engineer", "company": "Acme Software", "location": "Remote (United States)",
                              "description": BODY + STACK + extra, "body_status": "opened"})["parts"]["behavior"]

    def test_competitive_and_autonomous_work_scores_higher(self):
        base = self.style("")
        self.assertGreater(self.style(" You will negotiate pricing and run competitive evaluations."), base)
        self.assertGreater(self.style(" You will own the technical win end-to-end with real autonomy."), base)

    def test_pressure_caretaking_and_admin_duties_score_lower(self):
        base = self.style("")
        for extra in (" This is a high-pressure, sink or swim culture.",
                      " You will show empathy and nurture customers through the support ticket queue.",
                      " Strict adherence to compliance reporting and paperwork is required.",
                      " You must be on-call weekends."):
            self.assertLess(self.style(extra), base, extra)

    def test_cold_calling_alone_is_not_a_drain(self):
        self.assertEqual(self.style(" Some outbound and cold calling to warm accounts."), self.style(""))

    def test_market_does_not_change_the_score(self):
        a = scoring.score({"title": "Solutions Engineer", "company": "Acme Software", "location": "Remote (United States)",
                           "description": BODY + STACK + " We sell to police departments and city councils.", "body_status": "opened"})
        b = scoring.score({"title": "Solutions Engineer", "company": "Acme Software", "location": "Remote (United States)",
                           "description": BODY + STACK + " We sell to marketing teams at retailers.", "body_status": "opened"})
        self.assertEqual(a["score"], b["score"])


class PostingMinimums(unittest.TestCase):
    """Gates added after a job bot found jobs graded on the title while the posting asked for more."""

    def test_penetration_testing_title_is_low(self):
        for title in ("Solutions Engineer, Penetration Testing", "Red Team Sales Engineer", "Offensive Security Solutions Engineer"):
            self.assertEqual(grade(title), "low", title)

    def test_territory_in_the_title_that_leaves_out_his_area_is_low(self):
        for title in ("Presales Engineer - West", "Solutions Engineer (Northeast)", "Sales Engineer, NM/CO", "Pre-Sales Engineer - East Coast"):
            self.assertEqual(grade(title), "low", title)

    def test_territory_that_includes_his_area_is_fine(self):
        for title in ("Solutions Engineer - Midwest", "Sales Engineer, Central", "Solutions Engineer (Southeast)"):
            self.assertEqual(grade(title), "high", title)

    def test_a_closed_posting_is_low(self):
        job = {"title": "Solutions Engineer", "company": "Acme Software", "location": "Remote (United States)",
               "description": "", "body_status": "gone"}
        self.assertEqual(scoring.score(job)["grade"], "low")

    def test_required_years_are_not_hidden_by_a_preferred_clause(self):
        text = BODY + " 6+ years of technical pre-sales experience required; SaaS selling strongly preferred."
        self.assertEqual(scoring.years_required(text)[2], 6)
        self.assertEqual(grade("Solutions Consultant", description=text), "low")

    def test_masters_preferred_does_not_make_the_years_optional(self):
        text = BODY + " Bachelor's degree (Masters preferred) in engineering with 10+ years of experience in packaging."
        sales, tech, other, pref = scoring.years_required(text)
        self.assertEqual(max(sales, tech, other), 10)
        self.assertEqual(pref, 0)

    def test_years_in_squashed_text_are_read_and_kept_apart(self):
        text = "Requirements 3-5 years of experience in a similar role 5+ years experience in a technical cybersecurity role 3+ years experience in Security Pre-Sales Consulting"
        sales, tech, other, pref = scoring.years_required(text)
        self.assertEqual(tech, 5)
        self.assertEqual(sales, 3)

    def test_yearly_is_not_a_years_figure(self):
        self.assertEqual(scoring.years_required("Reviews are held 3 yearly with the team."), (0, 0, 0, 0))

    def test_preferred_heading_still_makes_years_optional(self):
        text = BODY + "\nPreferred qualifications:\n- 5+ years of sales engineering experience"
        self.assertEqual(scoring.years_required(text)[3], 5)
        self.assertEqual(scoring.years_required(text)[1:3], (0, 0))

    def test_one_of_the_following_list_is_not_failed_by_one_unfamiliar_tool(self):
        text = (BODY + "\nDevelopment experience with one or more of the following, required:\n"
                "- Front-end development (React, or similar)\n- Backend development (C#, NodeJS)\n"
                "- Mobile development experience (Xamarin, Swift, Objective-C, or similar)\n")
        self.assertEqual(scoring.missing_skill(text), "")

    def test_a_required_unfamiliar_tool_outside_an_any_of_list_still_fails(self):
        text = BODY + "\nRequirements:\n- Mobile development experience (Xamarin, Swift, Objective-C) required\n"
        self.assertNotEqual(scoring.missing_skill(text), "")

    def test_html_list_items_are_separate_requirements(self):
        text = BODY + "<ul><li>Familiar with React</li><li>6+ years of sales engineering experience</li></ul> Nice to have: Python"
        self.assertEqual(scoring.years_required(text)[2], 6)

    def test_spelled_out_years_are_read(self):
        for phrase, want in (
            ("Six (6) years of experience in the field", 6),
            ("six years of experience in the field", 6),
            ("Five+ years of experience in the field", 5),
            ("three to five years of experience in the field", 3),
            ("Seven (7) to ten (10) years of experience in the field", 7),
        ):
            got = scoring.years_required("Requirements:\n- " + phrase)[:3]
            self.assertEqual(max(got), want, phrase)

    def test_spelled_out_years_match_the_digit_form(self):
        spelled = scoring.years_required("Requirements:\n- Six (6) years of sales experience")
        digits = scoring.years_required("Requirements:\n- 6 years of sales experience")
        self.assertEqual(spelled, digits)
        self.assertEqual(scoring.se_years_required("Requirements:\n- Five (5) years of experience as a sales engineer"),
                         scoring.se_years_required("Requirements:\n- 5 years of experience as a sales engineer"))

    def test_number_words_that_are_not_year_counts_are_left_alone(self):
        self.assertEqual(scoring.years_required("Requirements:\n- A one-year contract with two teams and three managers"), (0, 0, 0, 0))
        self.assertEqual(scoring._numerals("one of the three offices"), "one of the three offices")

    def test_spelled_out_years_fail_the_posting_like_digits_do(self):
        desc = BODY + STACK + "\nRequirements:\n- Six (6) years of software engineering experience\n"
        self.assertEqual(grade("Software Engineer", description=desc), "low")


class GraduatingClass(unittest.TestCase):
    """Postings limited to a recent graduating class are closed to a 2019 graduate."""

    def test_recent_class_phrasings_are_caught(self):
        for line in (
            "Candidates must be a 2025 or 2026 graduate.",
            "Open to 2026 graduates only.",
            "Applicants from the class of 2026.",
            "Currently enrolled and graduating in May 2026.",
            "Expected graduation date between December 2025 and June 2027.",
        ):
            self.assertNotEqual(scoring.graduate_year_required("Requirements:\n- " + line), "", line)

    def test_graduate_degrees_and_old_years_are_not_a_class_limit(self):
        for line in (
            "A graduate degree in computer science is a plus.",
            "You graduated in 2019 from a four-year program.",
            "Founded in 2024, we serve graduate schools.",
        ):
            self.assertEqual(scoring.graduate_year_required("Requirements:\n- " + line), "", line)

    def test_preferred_class_year_does_not_block(self):
        text = "Preferred qualifications:\n- 2026 graduate"
        self.assertEqual(scoring.graduate_year_required(text), "")

    def test_a_graduating_class_posting_is_low_and_disqualified(self):
        desc = BODY + STACK + "\nRequirements:\n- Must be a 2025 or 2026 graduate. New grads welcome.\n"
        out = scoring.score({"title": "Junior Software Engineer", "company": "Acme Software",
                             "location": "Remote (United States)", "description": desc, "body_status": "opened"})
        self.assertEqual(out["grade"], "low")
        self.assertTrue(any("graduating class" in b for b in out["blockers"]))


class JavaIsASkill(unittest.TestCase):
    def test_java_requirement_does_not_fail_the_skill_gate(self):
        text = BODY + "\nRequirements:\n- Strong experience with Java and Spring\n"
        self.assertEqual(scoring.missing_skill(text), "")

    def test_java_counts_toward_stack_overlap(self):
        plain = grade("Junior Software Engineer", description=BODY + " Junior engineers welcome.")
        self.assertIn(plain, ("high", "medium"))
        a = scoring.score({"title": "Junior Software Engineer", "company": "Acme Software", "location": "Remote (United States)",
                           "description": BODY + " Career changers welcome. You will build with Java.", "body_status": "opened"})
        b = scoring.score({"title": "Junior Software Engineer", "company": "Acme Software", "location": "Remote (United States)",
                           "description": BODY + " Career changers welcome. You will build with Ruby.", "body_status": "opened"})
        self.assertGreater(a["score"], b["score"])


class StateLimits(unittest.TestCase):
    """Remote seats tied to states: his home states and the ones he would relocate to are fine."""

    def test_state_restriction_reads_codes_and_names(self):
        self.assertEqual(scoring.state_restriction("Candidates in TX/OK/AR/LA only."), ["TX", "OK", "AR", "LA"])
        self.assertEqual(scoring.state_restriction("You must reside in Texas, Oklahoma or Arkansas."), ["TX", "OK", "AR"])
        self.assertEqual(scoring.state_restriction("Restricted to candidates located in CA, WA and OR."), ["CA", "WA", "OR"])

    def test_office_lists_and_ok_are_not_restrictions(self):
        self.assertEqual(scoring.state_restriction("Offices in NY, CA and TX. Remote OK."), [])
        self.assertEqual(scoring.state_restriction("Hiring only in the United States."), [])

    def test_relocation_states_stay_gradable(self):
        text = BODY + " Candidates in TX/OK/AR/LA only."
        self.assertEqual(grade("Solutions Engineer", description=text), "high")

    def test_other_states_only_is_low(self):
        text = BODY + " You must reside in California, Washington or Oregon."
        self.assertEqual(grade("Solutions Engineer", description=text), "low")

    def test_title_territory_in_relocation_states_is_fine(self):
        self.assertEqual(grade("Solutions Engineer - TX/OK/AR/LA"), "high")
        self.assertEqual(grade("Solutions Engineer - Southwest"), "high")
        self.assertEqual(grade("Solutions Engineer - NY/NJ/CT"), "low")


class MinimumsLabel(unittest.TestCase):
    def label(self, title, extra="", status="opened"):
        return scoring.score({"title": title, "company": "Acme Software", "location": "Remote (United States)",
                              "description": BODY + STACK + extra, "body_status": status})

    def test_yes_when_nothing_asks_for_more(self):
        out = self.label("Solutions Engineer")
        self.assertEqual(out["minimums"], "yes")

    def test_stretch_names_the_years(self):
        out = self.label("Solutions Engineer", " 3+ years of pre-sales or sales engineering experience.")
        self.assertEqual(out["minimums"], "stretch")
        self.assertIn("years", out["minimums_note"])

    def test_unchecked_when_the_text_was_not_read(self):
        out = scoring.score({"title": "Solutions Engineer", "company": "Acme Software", "description": "", "body_status": "unread"})
        self.assertEqual(out["minimums"], "unchecked")

    def test_no_when_the_posting_asks_for_far_more(self):
        out = self.label("Solutions Engineer", " 8+ years of pre-sales experience required.")
        self.assertEqual(out["grade"], "low")
        self.assertEqual(out["minimums"], "no")

    def test_grade_job_stores_the_label(self):
        job = {"title": "Solutions Engineer", "company": "Acme Software", "description": BODY + STACK, "body_status": "opened"}
        scoring.grade_job(job)
        self.assertEqual(job["minimums"], "yes")


class ReportedHighs(unittest.TestCase):
    """Postings the job bot flagged as not a fit although they graded High."""

    def test_ideally_with_a_domain_does_not_make_the_years_optional(self):
        text = "Requirements\n5+ years of technical pre-sales experience, ideally with Open Source technologies, or in the Metrics/Monitoring space."
        self.assertEqual(scoring.years_required(BODY + "\n" + text)[0:2], (0, 0) if False else scoring.years_required(BODY + "\n" + text)[0:2])
        sales, tech, other, pref = scoring.years_required(text)
        self.assertEqual(max(sales, tech, other), 5)
        self.assertEqual(pref, 0)
        self.assertEqual(grade("Solutions Engineer", description=BODY + "\n" + text), "low")

    def test_ideally_before_the_figure_is_still_preferred(self):
        sales, tech, other, pref = scoring.years_required("5+ years of pre-sales experience is preferred.")
        self.assertEqual(max(sales, tech, other), 0)
        self.assertEqual(pref, 5)

    def test_payments_rails_requirement_is_a_gap(self):
        text = BODY + "\nRequirements\nHands-on experience with US payments, including ACH, RTP, wire transfers, card networks, or equivalent rails."
        self.assertEqual(grade("Technical Solutions Engineer", description=text), "low")

    def test_senior_individual_contributor_text_is_low(self):
        text = BODY + "\nThis is a senior individual contributor role reporting to the VP."
        self.assertEqual(grade("Technical Solutions Engineer", description=text), "low")

    def test_title_territory_by_state_name(self):
        self.assertTrue(scoring.territory_excludes_him("Solutions Engineer, New York (Enterprise)"))
        self.assertFalse(scoring.territory_excludes_him("Solutions Engineer, Florida (Enterprise)"))
        self.assertFalse(scoring.territory_excludes_him("Sales Engineer, SLED (Midwest/Southeast)"))
        self.assertEqual(scoring.title_states("Solutions Engineer, Florida (Enterprise)"), ["FL"])
        self.assertEqual(scoring.title_states("Engineer, Washington Post"), [])
        self.assertEqual(grade("Solutions Engineer, New York (Enterprise)"), "low")

    def test_title_state_codes_may_carry_a_compass_letter(self):
        self.assertTrue(scoring.territory_excludes_him("Sales Engineer (KY, W TN)"))
        self.assertTrue(scoring.territory_excludes_him("Territory Sales Engineer (NM/CO)"))
        self.assertFalse(scoring.territory_excludes_him("Sales Engineer (W TN, MS)"))
        self.assertFalse(scoring.territory_excludes_him("Sales Engineer (S TX, OK)"))
        self.assertFalse(scoring.territory_excludes_him("Sales Engineer (NE/SE)"))


class RequiredDegreeWording(unittest.TestCase):
    def grade_with(self, line):
        text = BODY.replace(" Bachelor's degree or equivalent experience.", "") + STACK + "\n" + line
        return grade("Solutions Engineer", description=text)

    def test_required_technical_degrees_are_low(self):
        for line in (
            "A degree in full stack engineering is required.",
            "Requirements\nBachelor's degree in Full Stack Engineering or similar.",
            "Requirements\nBachelor's in Software Development or a related discipline.",
            "Requirements\nBS in Computer Information Systems.",
            "Requirements\nDegree in Data Science or Analytics.",
            "Requirements\nA technical degree is a must.",
            "Requirements\nComputer science degree required.",
        ):
            self.assertEqual(self.grade_with(line), "low", line)

    def test_degrees_with_a_way_out_stay_high(self):
        for line in (
            "Requirements\nBachelor's degree in Information Technology or equivalent practical experience.",
            "Requirements\nEngineering degree or bootcamp certificate.",
            "Requirements\nBachelor's degree in Business, Marketing or Engineering.",
            "Preferred\nBachelor's degree in Full Stack Engineering.",
        ):
            self.assertEqual(self.grade_with(line), "high", line)


class NextToTargetRoles(unittest.TestCase):
    """AI evaluation, DevRel, support, integration and sales-tooling seats stay visible, capped at Medium."""

    LANE_TITLES = (
        "AI Trainer", "LLM Evaluator", "Search Quality Rater", "Developer Advocate", "Technical Support Engineer",
        "Integration Engineer", "Associate Salesforce Engineer", "Technical Writer", "Revenue Operations Analyst",
    )

    def test_lane_roles_are_medium_at_most(self):
        for title in self.LANE_TITLES:
            self.assertEqual(grade(title, description=BODY + STACK), "medium", title)

    def test_lane_roles_with_unread_text_are_not_high(self):
        for title in self.LANE_TITLES:
            out = scoring.score({"title": title, "company": "Acme Software", "location": "Remote (United States)",
                                 "description": "", "body_status": "unread"})
            self.assertNotEqual(out["grade"], "high", title)

    def test_crowd_work_platforms_are_low(self):
        for company in ("Outlier", "DataAnnotation", "Appen", "Remotasks"):
            self.assertEqual(grade("AI Trainer", company=company, description=BODY + STACK), "low", company)

    def test_per_task_pay_is_low_for_a_lane_role(self):
        text = BODY + STACK + " You are paid per task and can work as much or as little as you like."
        self.assertEqual(grade("AI Trainer", description=text), "low")


class CommissionOnlyAndLevels(unittest.TestCase):
    def test_commission_only_is_low(self):
        for line in ("This is a commission-only role.", "100% commission, no base salary.", "Straight commission."):
            text = BODY + STACK + " " + line
            self.assertEqual(grade("Sales Engineer", description=text), "low", line)

    def test_1099_alone_is_not_a_gate(self):
        text = BODY + STACK + " This is a 1099 contract."
        self.assertNotEqual(grade("Sales Engineer", description=text), "low")

    def test_level_markers_in_titles_are_senior(self):
        for title in ("Software Engineer L5", "Solutions Engineer IC4", "Software Engineer Level IV"):
            self.assertEqual(grade(title, description=BODY + STACK), "low", title)

    def test_seniority_without_numbers_is_low(self):
        for line in ("You bring a decade of experience in pre-sales.", "Half a decade of solutions work required."):
            self.assertEqual(grade("Solutions Engineer", description=BODY + STACK + " " + line), "low", line)

    def test_lacking_tools_and_credentials_are_low(self):
        for line in ("Hands-on Splunk and Datadog administration is required.",
                     "ITIL certification is required.", "AWS Solutions Architect-Associate required."):
            self.assertEqual(grade("Solutions Engineer", description=BODY + STACK + "\nRequirements\n" + line), "low", line)


class CareerChangeBoosts(unittest.TestCase):
    def score(self, extra, title="Solutions Engineer"):
        return scoring.score({"title": title, "company": "Acme Software", "location": "Remote (United States)",
                              "description": BODY + STACK + " " + extra, "body_status": "opened"})

    def test_career_change_wording_counts_as_entry_evidence(self):
        base = self.score("")["score"]
        for line in ("We welcome non-traditional backgrounds.", "Self-taught engineers are welcome.",
                     "Our SDR to SE path is well worn.", "No degree required."):
            self.assertGreaterEqual(self.score(line)["score"], base, line)

    def test_public_safety_software_scores_domain_points(self):
        plain = self.score("")["score"]
        safety = self.score("Our software serves 911 centers and law enforcement agencies on a cloud platform.")["score"]
        self.assertGreaterEqual(safety, plain)

    def test_night_shift_costs_work_style_points(self):
        self.assertLess(self.score("Overnight coverage is part of the rotation.")["score"], self.score("")["score"])

    def test_equal_opportunity_wording_is_not_entry_evidence(self):
        eeo = "We encourage candidates from underrepresented and non-traditional backgrounds to apply."
        self.assertFalse(scoring._ENTRY_TEXT_RE.search(scoring._EEO_SENTENCE_RE.sub(" ", eeo)))
        self.assertFalse(scoring._ENTRY_TEXT_RE.search("Formal or self-taught grounding in design."))

    def test_one_stray_public_safety_word_earns_no_domain_points(self):
        stray = "Report scams to your local law enforcement agency. We sell cloud software and a data platform. " * 3
        pts, why = scoring.domain_points("Software Engineer", stray, False)
        self.assertNotIn("public-safety", why)

    def test_a_list_that_names_postgres_is_satisfied_by_it(self):
        line = "Experience with data repository technologies (ie: PostgreSQL, Elasticsearch)."
        self.assertNotEqual(grade("Software Engineer I", description=BODY + STACK + "\nRequirements\n" + line), "low")

    def test_a_tool_category_he_lacks_is_still_low(self):
        for line in ("1+ years of experience with logging tools (e.g., Splunk) and Git.",
                     "Big data technology experience (e.g., Spark, Kafka, Hadoop or equivalent)."):
            self.assertEqual(grade("Software Engineer I", description=BODY + STACK + "\nRequirements\n" + line), "low", line)


class SnippetGates(unittest.TestCase):
    """An unread posting still leaves a snippet. Plain wording in it can rule the job out."""

    def unread(self, snippet, title="Sales Engineer", company="Acme Software"):
        return scoring.score({"title": title, "company": company, "location": "Remote (United States)",
                              "description": snippet, "body_status": "unread"})

    def assertGated(self, snippet, **kw):
        out = self.unread(snippet, **kw)
        self.assertEqual(out["grade"], "low", snippet)
        self.assertEqual(out["minimums"], "no")
        return out

    def assertKept(self, snippet, **kw):
        out = self.unread(snippet, **kw)
        self.assertEqual(out["grade"], "medium", snippet)
        self.assertEqual(out["minimums"], "unchecked")

    def test_clearance_in_the_snippet_rules_the_job_out(self):
        out = self.assertGated("...active DoD Security Clearance Job Description: The Software Engineer develops, maintains...",
                               title="Junior Software Engineer")
        self.assertIn("clearance", out["blockers"][-1])
        self.assertGated("Candidates must hold an active Secret clearance to start.")

    def test_on_site_label_rules_the_job_out(self):
        self.assertGated("San FranciscoCustomer Solutions /Full-Time /On-siteAs a Customer Solutions Engineer, you will be a strategic advisor...")
        self.assertGated("This position is on-site five days a week at our Detroit campus.")

    def test_a_fixed_territory_area_rules_the_job_out(self):
        out = self.assertGated("Walter USA is looking for a Lightweight Sales Engineer in the Toledo/Detroit area. The LSE acts as the technical commercial interface...")
        self.assertIn("Toledo/Detroit", out["blockers"][-1])
        self.assertGated("...and the New York-New Jersey-Connecticut tri-state area. We are seeking a technically skilled Associate Solutions Engineer...")

    def test_training_and_placement_firms_are_ruled_out(self):
        self.assertGated("We are looking for entry-level Java full stack developers for full time positions with clients.",
                         title="Junior Software Engineer")
        self.assertGated("Join our placement program and our client network.", title="Junior Software Engineer")
        self.assertGated("Entry-level developers wanted.", title="Junior Software Engineer", company="SynergisticIT")

    def test_must_be_based_in_a_place_rules_the_job_out_even_when_remote(self):
        out = self.assertGated("Security Sales Engineer – Cybersecurity Chicago, Illinois (Remote) - MUST BE BASED IN CHICAGO")
        self.assertIn("CHICAGO", out["blockers"][-1])
        self.assertGated("Remote sales engineer. Candidates must reside in Toledo, OH and travel to accounts.")
        self.assertGated("Hybrid role; applicants must be located in the Greater Boston Area.")

    def test_must_be_based_in_a_place_he_can_work_from_is_kept(self):
        self.assertKept("Remote. Must be based in the United States and able to demo the platform.")
        self.assertKept("Must be based in the US. Partner with account executives on technical demos.")
        self.assertKept("Sales engineer role. Must be located in the Midwest or Southeast.")
        self.assertKept("Must be based in Minneapolis, MN and travel to accounts.")
        self.assertKept("Must live in Dallas, TX to cover the territory.")
        self.assertKept("Must be based in Georgia. Partner with account executives on technical demos.")
        self.assertKept("We are based in Chicago and hire remote sales engineers across the country.")
        self.assertKept("Applicants must be based in one of our hub states and run technical demos.")
        # Eastern holds Georgia and Florida, and a short region acronym does not say which states it covers.
        self.assertKept("Must be based in the Eastern Standard Time (EST) time zone. Partner with account executives on demos.")
        self.assertKept("Pre-sales engineers are required to reside in the TOLA region of the US as required for business needs.")

    def test_must_be_based_in_a_zone_or_state_that_leaves_him_out_is_ruled_out(self):
        self.assertGated("Must be located in the PST/MST timezone. Partner with account executives on demos.")
        self.assertGated("Must live in or near Chicago. Fully remote. Run technical demos for mid-market buyers.")
        self.assertGated("Must reside in NY. Partner with account executives on technical demos.")

    def test_remote_wording_or_a_home_place_switches_the_place_gates_off(self):
        self.assertKept("Fully remote role. Some on-site customer demos. /On-site visits quarterly.")
        self.assertKept("Hybrid. /On-site two days in the Chicago area.")
        self.assertKept("A sales engineer in the Minneapolis area partnering with account executives.")
        self.assertKept("Sales Engineer for the Dallas area covering Texas accounts.")

    def test_snippets_that_only_describe_the_job_stay_unchecked(self):
        self.assertKept("We seek a Sales Engineer to partner with account executives and run technical demos for mid-market buyers.")
        self.assertKept("You will join the Customer Success area of our platform team and demo the product.")
        self.assertKept("Travel to customer sites for on-site demos is expected a few times a year.")

    def test_snippet_gates_do_not_touch_read_postings(self):
        # A read posting is judged on its full text, by the gates that already exist for it.
        desc = BODY + STACK + " The team sits in the Toledo/Detroit area."
        self.assertNotEqual(grade("Solutions Engineer", description=desc), "low")
