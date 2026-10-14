"""research: the csv block of a startups report is merged into the file the tracker's Startups page reads."""

import pandas as pd

from job_radar import research

HEADER = "region,name,city,size,kind,what,careers,website,source"
REPORT = f'Report text.\n```csv\n{HEADER}\nGulf,Acme,"Dubai, AE",12,AI/data,"does, things",,https://a.co,test\nEgypt,34ML,Cairo,60,product,new,,,test\n```'


def test_new_names_are_added_and_a_known_name_is_replaced_in_place_of_the_old_row(tmp_path):
    file = tmp_path / "startups.csv"
    file.write_text(f"{HEADER}\nEgypt,34ml,Giza,50,AI/data,old,,,old\nGulf,Other,Doha,9,product,kept,,,old\n", encoding="utf-8")
    assert research.merge_csv(REPORT, file) == 2
    rows = pd.read_csv(file, dtype=str).fillna("")
    assert list(rows.columns) == HEADER.split(",")
    assert sorted(rows["name"]) == ["34ML", "Acme", "Other"]
    assert rows.set_index("name").loc["34ML", "what"] == "new" and rows.set_index("name").loc["Acme", "city"] == "Dubai, AE"


def test_a_name_twice_in_the_old_file_is_left_alone(tmp_path):
    file = tmp_path / "startups.csv"
    file.write_text(f"{HEADER}\nEgypt (office),Twin,Riyadh,42,product,a,,,old\nGulf,Twin,Riyadh,42,product,a,,,old\n", encoding="utf-8")
    research.merge_csv(REPORT, file)
    assert (pd.read_csv(file, dtype=str)["name"] == "Twin").sum() == 2


def test_a_report_without_a_csv_block_changes_nothing(tmp_path):
    file = tmp_path / "startups.csv"
    assert research.merge_csv("no block here", file) == 0 and not file.exists()
