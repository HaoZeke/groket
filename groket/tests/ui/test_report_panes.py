"""Report markdown split into selectable pane bodies."""

from __future__ import annotations

from groket.ui.report_panes import split_report_markdown_panes


def test_split_empty() -> None:
    assert split_report_markdown_panes("") == []
    assert split_report_markdown_panes("   ") == []


def test_split_single_block_no_h2() -> None:
    md = "# Title\n\nJust a body without H2 sections.\n"
    assert split_report_markdown_panes(md) == [md.strip()]


def test_split_on_h2() -> None:
    md = "# Head\n\nintro\n\n## Session summary\n\nsummary body\n\n## Other\n\nmore\n"
    panes = split_report_markdown_panes(md)
    assert len(panes) == 3
    assert panes[0].startswith("# Head")
    assert "intro" in panes[0]
    assert panes[1].startswith("## Session summary")
    assert "summary body" in panes[1]
    assert panes[2].startswith("## Other")
    assert "more" in panes[2]


def test_split_mf_form_and_issue_fences() -> None:
    """Form fields + Issue box fences become paste-ready panes of their own."""
    md = """# Model Feedback form drafts — sess

Paste **one issue block** into each Model Feedback submission.

## Session summary

Overall: two issues worth filing.

## Issue 1: Ignored MCP

### Form fields (copy line-by-line)

```
Model Name: grok-test
Session ID: sess
Severity: Major
Category (up to 3): Instruction Following
```

### Issue (copy into the Issue box)

```
What: Claimed MCP failed without trying the bridge.
Where: Turn 0
Why: Instruction required MCP-first.
Should have: Call preferred MCP tools first.
Pattern: Asserts preferred integration is down without attempting it
```

_Evidence (reference only): #4, #32_

## Issue 2: Second problem

### Form fields (copy line-by-line)

```
Model Name: grok-test
Severity: Minor
```

### Issue (copy into the Issue box)

```
What: Second what
Where: Turn 1
Why: Second why
Should have: Fix it
Pattern: none
```
"""
    panes = split_report_markdown_panes(md)
    # preamble, summary, issue1 header, form1, issue1 box, evidence?, issue2 header, form2, issue2 box
    assert any(p.startswith("# Model Feedback") for p in panes)
    assert any(p.startswith("## Session summary") for p in panes)

    form_panes = [p for p in panes if p.startswith("Model Name:")]
    assert len(form_panes) == 2
    assert "Category (up to 3): Instruction Following" in form_panes[0]
    assert "Severity: Minor" in form_panes[1]

    issue_panes = [p for p in panes if p.startswith("What:")]
    assert len(issue_panes) == 2
    assert "What: Claimed MCP failed" in issue_panes[0]
    assert "Should have: Call preferred MCP tools first." in issue_panes[0]
    assert "What: Second what" in issue_panes[1]
    # Fence chrome must not wrap the paste body
    for p in issue_panes + form_panes:
        assert "```" not in p
        assert "### Form fields" not in p
        assert "### Issue" not in p


def test_issue_pane_is_paste_ready_without_form() -> None:
    md = """## Issue 1: Alone

### Issue (copy into the Issue box)

```
What: only issue
Where: here
Why: because
Should have: better
Pattern: x
```
"""
    panes = split_report_markdown_panes(md)
    assert any(p.startswith("What: only issue") for p in panes)
    assert any(p.startswith("## Issue 1:") for p in panes)
