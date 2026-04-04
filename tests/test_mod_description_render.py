from mod_search.description_render import render_mod_description


def test_render_empty_description_uses_fallback():
    html = render_mod_description("")
    assert "Sin descripci" in html


def test_render_markdown_features():
    raw = """# Planetary Pioneers

![Banner](https://example.com/banner.png)

- First
- Second

Visit [site](https://example.com).
    """
    html = render_mod_description(raw)
    assert "<h1>Planetary Pioneers</h1>" in html
    assert 'src="https://example.com/banner.png"' in html
    assert 'alt="Banner"' in html
    assert "<ul>" in html
    assert '<a href="https://example.com">site</a>' in html


def test_render_code_block_and_quote():
    raw = """> quoted

```
11111
```"""
    html = render_mod_description(raw)
    assert "<blockquote>quoted</blockquote>" in html
    assert "<pre><code>11111</code></pre>" in html


def test_render_html_sanitizes_unsafe_content():
    raw = '<p>Hello</p><script>alert(1)</script><a href="javascript:alert(1)">bad</a><img src="https://example.com/x.png" onclick="x()">'
    html = render_mod_description(raw)
    assert "<script" not in html
    assert "javascript:alert" not in html
    assert "onclick" not in html
    assert 'src="https://example.com/x.png"' in html
