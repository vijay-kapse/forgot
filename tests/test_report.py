from forgot.model import Evidence, Suggestion
from forgot.report import as_text, headline


def suggestion(path="tests/test_auth.py", confidence=0.82):
    return Suggestion(
        path=path,
        confidence=confidence,
        lift=4.2,
        evidence=Evidence("src/auth.py", 22, 84),
    )


def test_headline_agrees_with_itself_grammatically():
    assert headline(1) == "1 file usually changes with this edit but is not staged."
    assert headline(3) == "3 files usually change with this edit but are not staged."


def test_text_report_shows_both_numbers_and_explains_the_gap():
    out = as_text(["src/auth.py"], [suggestion()], color=False)
    assert "82%" in out
    assert "22 of 84 commits touching src/auth.py" in out
    assert "weight recent commits above old ones" in out


def test_text_report_offers_the_fix_and_the_escape_hatch():
    out = as_text(["src/auth.py"], [suggestion()], color=False)
    assert "git add tests/test_auth.py" in out
    assert "--no-verify" in out
    assert ".forgotignore" in out
    assert "forgot why src/auth.py tests/test_auth.py" in out


def test_empty_suggestions_produce_no_output():
    assert as_text(["src/auth.py"], [], color=False) == ""


def test_color_codes_only_when_asked():
    assert "\033[" in as_text(["a.py"], [suggestion()], color=True)
    assert "\033[" not in as_text(["a.py"], [suggestion()], color=False)


def test_json_note_documents_the_two_metrics():
    import json

    from forgot.report import as_json

    payload = json.loads(as_json(["src/auth.py"], [suggestion()], 500))
    assert "recency-weighted" in payload["note"]
    assert payload["missing"][0]["confidence"] == 0.82
