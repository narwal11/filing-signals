from parse import html_to_text


def non_empty_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line]


def test_script_and_style_removed():
    html = "<html><head><style>p {color: red}</style></head><body><script>alert(1)</script><p>Hello</p></body></html>"
    assert html_to_text(html) == "Hello"


def test_head_and_title_removed():
    html = (
        "<html><head><title>aapl-20250927</title><meta charset='utf-8'></head>"
        "<body><p>Cover page</p></body></html>"
    )
    assert html_to_text(html) == "Cover page"


def test_title_removed_without_head():
    assert html_to_text("<title>aapl-20250927</title><p>Cover page</p>") == "Cover page"


def test_display_none_removed():
    html = (
        '<div style="display:none">secret</div>'
        '<div style="color: red; DISPLAY : NONE;">also secret</div>'
        "<p>visible</p>"
    )
    assert html_to_text(html) == "visible"


def test_ix_header_removed():
    html = (
        "<body><ix:header><ix:hidden>dei:EntityCentralIndexKey 0000320193</ix:hidden></ix:header>"
        "<p>Annual report</p></body>"
    )
    assert html_to_text(html) == "Annual report"


def test_hidden_div_containing_ix_header_removed():
    html = '<div style="display:none"><ix:header>meta</ix:header></div><p>body</p>'
    assert html_to_text(html) == "body"


def test_block_elements_on_own_lines():
    html = (
        "<div><p>one</p><p>two</p>three<br>four</div>"
        "<ul><li>a</li><li>b</li></ul><h2>Head</h2>"
    )
    assert non_empty_lines(html_to_text(html)) == ["one", "two", "three", "four", "a", "b", "Head"]


def test_inline_tags_stay_on_one_line():
    assert html_to_text("<p>Net <b>sales</b> grew <span>5</span> percent</p>") == "Net sales grew 5 percent"


def test_source_newlines_inside_paragraph_become_spaces():
    assert html_to_text("<p>first\n   second</p>") == "first second"


def test_table_rows_become_lines():
    html = (
        "<table>"
        "<tr><th>Item</th><th>2022</th></tr>"
        "<tr><td>Revenue</td><td>100</td></tr>"
        "</table>"
    )
    assert html_to_text(html) == "Item | 2022\nRevenue | 100"


def test_table_fragments_merged():
    html = (
        "<table><tr>"
        "<td>Net income</td><td>$</td><td>1,200</td>"
        "<td></td><td>$</td><td>(</td><td>56</td><td>)</td>"
        "<td>12</td><td>%</td>"
        "</tr></table>"
    )
    assert html_to_text(html) == "Net income | 1,200 | (56) | 12%"


def test_row_with_only_empty_cells_is_skipped():
    html = "<table><tr><td></td><td>$</td></tr><tr><td>a</td><td>b</td></tr></table>"
    assert html_to_text(html) == "a | b"


def test_characters_normalized():
    html = "<p>Apple\xa0Inc.​’s “best” year ‘ever’</p>"
    assert html_to_text(html) == "Apple Inc.'s \"best\" year 'ever'"


def test_runs_of_spaces_collapsed():
    assert html_to_text("<p>a      b \t  c</p>") == "a b c"


def test_three_or_more_newlines_become_two():
    html = "<p>top</p><br><br><br><br><p>bottom</p>"
    assert html_to_text(html) == "top\n\nbottom"


def test_accepts_bytes():
    assert html_to_text("<p>café</p>".encode("utf-8")) == "café"
