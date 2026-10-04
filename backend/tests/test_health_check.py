"""The health check a PaaS host sends.

Render and its relatives probe a new deployment with `HEAD /` while it is
deciding whether anything is listening. FastAPI leaves a GET route as GET —
unlike Starlette's own router, which widens it — so the probe answers 405,
the host concludes there is no server, and it spends its startup budget
scanning ports that were working all along.

That is a real cost rather than a cosmetic warning: the budget is finite, and
the check is the thing standing between a slow cold start and a deployment
marked failed.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import home


def client() -> TestClient:
    app = FastAPI()
    app.include_router(home.router)
    return TestClient(app)


class TestTheRootPage:
    def test_a_person_still_gets_the_page(self):
        response = client().get("/")

        assert response.status_code == 200
        assert "backend is running" in response.text

    def test_the_host_probe_is_answered(self):
        # The probe that was failing. Not "should probably work".
        assert client().head("/").status_code == 200

    def test_the_probe_says_nothing_in_the_body(self):
        # HEAD carries no body by definition, and returning one only invites
        # a host to read it.
        assert client().head("/").content == b""


class TestHealthz:
    def test_it_answers(self):
        response = client().get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_it_can_be_probed_too(self):
        # A host configured with this path will send HEAD here as well.
        assert client().head("/healthz").status_code == 200