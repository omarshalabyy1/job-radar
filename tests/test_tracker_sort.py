"""Every list of jobs opens in one order (Omar, 2026-11-10): Egypt, then remote, then each other country on its own; inside each,
its jobs grouped by source (the job board), data and BI roles before AI, then entry level to senior."""
import pandas as pd

from tracker import common


def test_the_default_sort_is_egypt_remote_country_by_country_then_data_then_level():
    assert common.DEFAULT_SORT[:5] == ["Egypt first ↑", "Where ↑", "Source ↑", "Data and BI first ↑", "Level ↑"]
    assert common.SORTS["Egypt first"] == "place_rank" and common.SORTS["Where"] == "place"
    assert common.SORTS["Data and BI first"] == "role_group" and common.SORTS["Level"] == "level"


def test_role_group_puts_data_and_bi_before_ai_and_level_runs_entry_to_senior():
    roles = pd.Series(["AI & Data Engineer", "Data Engineer", "BI Developer", "Data Analyst", "AI Engineer (NLP, LLM, agents, RAG)",
                       "More from your LinkedIn alerts"])
    assert common.role_group(roles).tolist() == [0, 0, 0, 0, 1, 1]
    titles = ["Junior Data Engineer", "Data Engineer", "Senior Data Engineer", "Data Engineer", "Data Engineer"]
    years = pd.Series([None, None, None, 1, 6])
    assert common.levels(pd.Series(titles), years).tolist() == [0, 1, 2, 0, 2]


def test_default_order_is_one_rule_for_every_list():
    rows = [  # title, place, source, role_group, level, score
        ("UAE AI", "UAE", "bayt", 1, 0, 9), ("USA DE", "USA", "indeed", 0, 0, 5), ("UAE senior DE", "UAE", "bayt", 0, 2, 5),
        ("UAE junior DE", "UAE", "bayt", 0, 0, 5), ("Remote AI", "Remote", "himalayas", 1, 0, 5), ("Remote DE", "Remote", "himalayas", 0, 1, 5),
        ("Egypt AI wuzzuf", "Egypt", "wuzzuf", 1, 0, 5), ("Egypt DE senior wuzzuf", "Egypt", "wuzzuf", 0, 2, 5),
        ("Egypt DE mid high linkedin", "Egypt", "linkedin", 0, 1, 8), ("Egypt DE mid low linkedin", "Egypt", "linkedin", 0, 1, 3),
        ("Egypt DE junior wuzzuf", "Egypt", "wuzzuf", 0, 0, 5)]
    jobs = pd.DataFrame(rows, columns=["title", "place", "source", "role_group", "level", "score"])
    jobs["place_rank"] = jobs["place"].map({"Egypt": 0, "Remote": 1}).fillna(2)
    jobs["role_rank"], jobs["posted_at"] = 2, pd.Timestamp("2026-11-09")
    assert common.default_order(jobs)["title"].tolist() == [
        "Egypt DE mid high linkedin", "Egypt DE mid low linkedin",  # Egypt, source by source (linkedin, then wuzzuf): data, by level
        "Egypt DE junior wuzzuf", "Egypt DE senior wuzzuf", "Egypt AI wuzzuf",
        "Remote DE", "Remote AI",
        "UAE junior DE", "UAE senior DE", "UAE AI",  # then each country together: the UAE
        "USA DE"]
