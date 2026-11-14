"""A CV a line or two over one page is printed a little smaller instead of failing the run. On 2026-11-14 the Set Europa CV
ran 5 words onto a second page at 9.6pt (it fits at 9.3pt), tailor raised, and the whole collect ended failed."""

from job_radar import tailor
from tests.test_tailor import sample_draft


def fake_print(monkeypatch, pages_at: dict):
    """pdf_of returns the page itself; a page counts the pages its body font size is given in `pages_at` (else 1)."""
    printed = []
    monkeypatch.setattr(tailor, "pdf_of", lambda page: printed.append(page) or page.encode())
    monkeypatch.setattr(tailor, "pages_of", lambda pdf: next((n for size, n in pages_at.items()
                                                              if f"font-size: {size};".encode() in pdf), 1))
    return printed


def test_a_cv_that_fits_keeps_its_size(monkeypatch):
    printed = fake_print(monkeypatch, {})
    page = tailor.cv_html(sample_draft())
    assert tailor.fit_one_page(page) == (page, page.encode()) and len(printed) == 1


def test_a_cv_one_line_over_is_printed_one_size_smaller(monkeypatch):
    fake_print(monkeypatch, {"9.6pt": 2})
    fitted, pdf = tailor.fit_one_page(tailor.cv_html(sample_draft()))
    assert "font-size: 9.3pt;" in fitted and "font-size: 9.6pt;" not in fitted and pdf == fitted.encode()


def test_a_cv_far_too_long_stops_at_the_smallest_size_and_is_still_over(monkeypatch):
    printed = fake_print(monkeypatch, {size: 2 for size in tailor.FIT_SIZES})
    fitted, pdf = tailor.fit_one_page(tailor.cv_html(sample_draft()))
    assert f"font-size: {tailor.FIT_SIZES[-1]};" in fitted and len(printed) == len(tailor.FIT_SIZES)
    assert tailor.pages_of(pdf) == 2  # write_draft's check then says "is 2 pages, not 1", as before


def test_the_sizes_start_at_the_layouts_own_and_stay_readable():
    assert tailor.FIT_SIZES[0] == "9.6pt" and float(tailor.FIT_SIZES[-1][:-2]) >= 8.8
    assert f"font-size: {tailor.FIT_SIZES[0]};" in tailor.CV_CSS
