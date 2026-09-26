import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def _js_sources():
    return [WEB / "app.js", *sorted((WEB / "js").glob("*.js"))]


def test_every_dom_hook_used_by_js_exists_in_index_html():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'\bid="([^"]+)"', html))
    missing = []
    for src in _js_sources():
        js = src.read_text(encoding="utf-8")
        for dom_id in re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', js):
            if dom_id not in html_ids:
                missing.append(f"{src.name}: #{dom_id}")
        for kind, key in re.findall(r'\[data-(at|pos)="([^"$]+)"\]', js):
            if f'data-{kind}="{key}"' not in html:
                missing.append(f"{src.name}: [data-{kind}={key}]")
        for key in re.findall(r'atAll\("([^"]+)"\)', js):
            if f'data-at="{key}"' not in html:
                missing.append(f"{src.name}: [data-at={key}]")
        for key in re.findall(r'field\("([^"]+)"\)', js):
            if f'data-pos="{key}"' not in html:
                missing.append(f"{src.name}: [data-pos={key}]")
    assert not missing, "JS hooks with no element in index.html:\n" + "\n".join(missing)
