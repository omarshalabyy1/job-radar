from tracker.common import most_employees


def test_most_employees_reads_the_top_of_any_size_text():
    assert most_employees("51-200") == 200
    assert most_employees("251") == 251
    assert most_employees("200 Employees") == 200
    assert most_employees("1,000+") == float("inf")
    assert most_employees("10,001+ employees") == float("inf")
    assert most_employees("Unknown") is None and most_employees(None) is None
