"""Tests for the deterministic content-truthfulness checker (quote grounding).

Every test names both directions where relevant: a fabricated quote MUST be flagged and a
real one MUST pass, so the suite proves the check can distinguish, not merely stay green.
"""

from pxx.truthfulness import check_quote_grounding

_READ = 'def parse_config(path):\n    """Load the config."""\n    return {"key": read(path)}'
_DIFF = "+def added_helper(x):\n+    return x + 1"


def test_grounded_fenced_quote_passes():
    text = 'The file has:\n```python\ndef parse_config(path):\n    return {"key": read(path)}\n```'
    assert check_quote_grounding(text, [_READ]) == []


def test_fabricated_fenced_quote_flagged():
    # NEGATIVE CONTROL: code the model presents as existing, grounded nowhere it read/wrote
    text = "The file defines:\n```python\ndef delete_everything():\n    os.system('rm -rf /')\n```"
    findings = check_quote_grounding(text, [_READ, _DIFF])
    assert len(findings) == 1
    assert findings[0].kind == "fenced"
    assert "delete_everything" in findings[0].quote


def test_grounded_inline_code_passes():
    text = 'It calls `return {"key": read(path)}` at the end.'
    assert check_quote_grounding(text, [_READ]) == []


def test_ungrounded_inline_code_flagged():
    text = 'It calls `return {"secret": exfiltrate(path)}` at the end.'
    findings = check_quote_grounding(text, [_READ])
    assert len(findings) == 1 and findings[0].kind == "inline"


def test_prose_and_short_backticks_ignored():
    # a filename, a flag, and prose in backticks are not code quotes -> never flagged,
    # even though none appear in the (empty) sources
    text = "See `config.py`, pass `--verbose`, and note `the retry budget`."
    assert check_quote_grounding(text, [""]) == []


def test_whitespace_insensitive_grounding():
    # the model reflows indentation; still grounded -> no false positive
    text = '```python\ndef parse_config(path):\n        return {"key": read(path)}\n```'
    assert check_quote_grounding(text, [_READ]) == []


def test_quote_grounded_in_written_diff():
    # a quote of the agent's OWN new code is grounded by the diff, not a read file
    text = "I added:\n```python\ndef added_helper(x):\n    return x + 1\n```"
    assert check_quote_grounding(text, [_READ, _DIFF]) == []


def test_empty_text_and_no_sources():
    assert check_quote_grounding("", [_READ]) == []
    # ungrounded code with no sources at all -> flagged (nothing to ground against)
    assert len(check_quote_grounding("`def x(): return sekret()`", [])) == 1


def test_non_vacuous_can_pass_and_fail():
    # the check MUST be able to both pass a real quote and flag a fabricated one
    real = '```python\ndef parse_config(path):\n    return {"key": read(path)}\n```'
    fake = "```python\ndef never_existed():\n    return fabricated()\n```"
    assert check_quote_grounding(real, [_READ]) == []
    assert check_quote_grounding(fake, [_READ]) != []


def test_deduplicates_repeated_ungrounded_quote():
    text = "`return madeup(a)` ... and again `return madeup(a)`"
    assert len(check_quote_grounding(text, [_READ])) == 1
