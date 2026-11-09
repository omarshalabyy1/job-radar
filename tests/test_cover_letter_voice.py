"""The cover letter is asked for in Omar's own voice (2026-11-09, the ABB letter he approved): "Dear <Company> Hiring Team,",
formal corporate English that stays diplomatic and appreciative, about 200 words, Bosch Car Service then the real work at
Null Labs, thanks and kind regards."""
from job_radar import tailor


def letter_rules() -> str:
    return tailor.RULES.split("cover_letter_md:", 1)[1].split("notes_md:", 1)[0]


def test_the_letter_is_asked_for_in_his_corporate_diplomatic_voice():
    rules = letter_rules()
    for needed in ('"Dear <Company> Hiring Team,"', "formal corporate English", "no contractions",
                   "pleased to express", "supply chain and operations", "I would welcome the opportunity",
                   "time and consideration", "Kind regards"):
        assert needed in rules
    assert '"Hi Hiring Team,"' not in rules


def test_the_letter_shows_the_real_work_at_null_labs():
    rules = letter_rules()
    for needed in ("Apache Airflow", "Business Central", "bronze, silver and gold", "quarantine", "deductions",
                   "under human review"):
        assert needed in rules


def test_the_letter_has_its_own_voice_apart_from_the_cvs():
    assert "Voice, in all three" not in tailor.RULES  # the plain, direct voice is the CV's, notes' and form answers'
    assert "Voice of the cover letter" in tailor.RULES
