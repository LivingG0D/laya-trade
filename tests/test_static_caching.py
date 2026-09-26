from starlette.testclient import TestClient

import src.app as app_module


def test_ui_files_are_revalidated_so_deploys_never_mix_old_and_new_assets():
    # Without Cache-Control the browser may reuse a stale style.css/app.js next to a
    # fresh index.html (e.g. old CSS kept closed <dialog>s visible).
    client = TestClient(app_module.app)
    for path in ("/", "/static/style.css", "/static/app.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == "no-cache", path

    # Revalidation stays cheap: an unchanged file answers 304 to its ETag.
    etag = client.get("/static/style.css").headers["etag"]
    assert client.get("/static/style.css", headers={"If-None-Match": etag}).status_code == 304
