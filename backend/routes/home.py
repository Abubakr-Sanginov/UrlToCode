from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

router = APIRouter()


@router.get("/")
async def get_status():
    return HTMLResponse(
        content="<h3>Your backend is running correctly. Please open the front-end URL (default is http://localhost:5173) to use screenshot-to-code.</h3>"
    )


@router.head("/")
async def head_status():
    """Answer the health check a PaaS host sends.

    FastAPI does not widen a GET route to HEAD the way Starlette's own router
    does, so without this the host's probe gets a 405 and concludes there is
    nothing listening. It then burns its startup budget scanning other ports
    before finding the one that was answering the whole time.
    """
    return Response(status_code=200)


@router.get("/healthz")
async def healthz():
    """A health check that says what it is, for hosts that let one be set.

    Returns JSON rather than the html of the root page, so a status page that
    reports the container healthy can also be used to check that it is.
    """
    return {"status": "ok"}


@router.head("/healthz")
async def head_healthz():
    """The same reason as the one on `/`: whatever path a host is told to
    probe, it will probe with HEAD first."""
    return Response(status_code=200)