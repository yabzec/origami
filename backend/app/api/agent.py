import re
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import api_error, get_current_user, get_current_user_flexible
from app.config import get_settings
from app.models import User
from app.services.agent_hub import AgentHub, get_agent_hub

router = APIRouter(prefix="/api/agent", tags=["agent"])

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
AGENT_FILES = {
    "windows-amd64": "origami-agent-windows-amd64.exe",
    "linux-amd64": "origami-agent-linux-amd64",
    "linux-arm64": "origami-agent-linux-arm64",
    "darwin-arm64": "origami-agent-darwin-arm64.zip",
    "darwin-amd64": "origami-agent-darwin-amd64.zip",
}


class LaunchRequest(BaseModel):
    client_id: str


def _check_client_id(client_id: str) -> None:
    if not CLIENT_ID_RE.match(client_id):
        raise api_error(422, "invalid_client_id", "client_id must be 8-64 letters, digits or dashes")


@router.post("/launch")
def launch(
    body: LaunchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    hub: AgentHub = Depends(get_agent_hub),
) -> dict:
    _check_client_id(body.client_id)
    server = get_settings().public_url or str(request.base_url)
    query = urlencode({"server": server.rstrip("/"), "token": hub.issue_token(user.id, body.client_id)})
    return {"url": f"origami-agent://connect?{query}"}


@router.post("/discover", status_code=204)
def discover(
    body: LaunchRequest,
    user: User = Depends(get_current_user),
    hub: AgentHub = Depends(get_agent_hub),
) -> Response:
    _check_client_id(body.client_id)
    hub.request_discover(user.id, body.client_id)  # nothing to do when not connected
    return Response(status_code=204)


@router.websocket("/ws")
async def agent_socket(websocket: WebSocket, token: str = "", hub: AgentHub = Depends(get_agent_hub)) -> None:
    owner = hub.consume_token(token)
    await websocket.accept()
    if owner is None:
        await websocket.close(code=4401)
        return
    await hub.serve(websocket, owner[0], owner[1])


@router.get("/downloads")
def downloads(user: User = Depends(get_current_user)) -> dict:
    dist = get_settings().agent_dist_dir
    return {"platforms": [p for p, name in AGENT_FILES.items() if (dist / name).is_file()]}


@router.get("/download/{platform}")
def download(platform: str, user: User = Depends(get_current_user_flexible)) -> FileResponse:
    name = AGENT_FILES.get(platform)
    if name is None:
        raise api_error(404, "unknown_platform", f"No agent build for {platform}")
    path = get_settings().agent_dist_dir / name
    if not path.is_file():
        raise api_error(404, "agent_not_built", "The scanner agent has not been built on the server")
    return FileResponse(path, filename=name, media_type="application/octet-stream")
