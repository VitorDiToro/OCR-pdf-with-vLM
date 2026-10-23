from ocrpdf.htmlsafe import sanitize_table


def test_keeps_table_tags_and_drops_the_rest():
    html = '<div><table onclick="x"><tr><td colspan="2" style="color:red">a<br>b<script>c</script></td></tr></table></div>'
    assert sanitize_table(html) == '<table><tr><td colspan="2">a<br>bc</td></tr></table>'
    assert sanitize_table("<p>sem tabela</p>") == ""


def test_keeps_a_valid_align():
    html = '<table><tr align="center"><td align="RIGHT">CDD</td><td align="evil">x</td></tr></table><p align="left">'
    out = sanitize_table(html)
    assert '<tr align="center">' in out and '<td align="right">' in out and "evil" not in out
    assert sanitize_table(out) == out                     # idempotente: o editor regrava o que mostrou
