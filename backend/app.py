import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = FastAPI(title="AI YouTube Shorts API")

origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv(
    "YOUTUBE_REDIRECT_URI",
    "https://heh-heh.github.io/auto_youtube_sots/oauth/callback.html",
)
TOKEN_PATH = Path(
    os.getenv("YOUTUBE_TOKEN_PATH", "/home/ssm-user/youtube-shorts-data/youtube_token.json")
)
API_KEY = os.getenv("YOUTUBE_API_KEY", "")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# state -> PKCE code_verifier
oauth_states = {}


def oauth_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def load_credentials():
    if not TOKEN_PATH.exists():
        return None
    try:
        credentials = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception:
        return None
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_PATH.write_text(credentials.to_json())
            os.chmod(TOKEN_PATH, 0o600)
        except Exception:
            return None
    return credentials if credentials.valid else None


def require_api_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid API key")


def make_flow():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )
    flow.redirect_uri = REDIRECT_URI
    return flow


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "youtube-shorts",
        "oauth_configured": oauth_configured(),
        "youtube_connected": load_credentials() is not None,
    }


@app.get("/api/youtube/auth")
def youtube_auth(x_api_key: str | None = Header(default=None)):
    require_api_key(x_api_key)
    if not oauth_configured():
        raise HTTPException(status_code=500, detail="YouTube OAuth credentials are not configured.")

    flow = make_flow()
    state = secrets.token_urlsafe(32)

    # Google OAuth may use PKCE. authorization_url() creates the verifier.
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )

    if not flow.code_verifier:
        raise HTTPException(status_code=500, detail="OAuth PKCE verifier was not generated.")

    oauth_states[state] = flow.code_verifier
    return {"authorization_url": authorization_url, "state": state}


@app.post("/api/youtube/callback")
async def youtube_callback(
    payload: dict,
    x_api_key: str | None = Header(default=None),
):
    require_api_key(x_api_key)

    code = payload.get("code")
    state = payload.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state are required")

    code_verifier = oauth_states.pop(state, None)
    if not code_verifier:
        raise HTTPException(status_code=400, detail="invalid or expired OAuth state")

    flow = make_flow()
    flow.code_verifier = code_verifier

    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OAuth token exchange failed: {exc}")

    credentials = flow.credentials
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(credentials.to_json())
    os.chmod(TOKEN_PATH, 0o600)

    return {"ok": True, "connected": True}


@app.get("/api/youtube/status")
def youtube_status(x_api_key: str | None = Header(default=None)):
    require_api_key(x_api_key)
    credentials = load_credentials()
    if not credentials:
        return {"connected": False, "message": "YouTube OAuth credentials are required."}

    try:
        youtube = build("youtube", "v3", credentials=credentials)
        response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
        items = response.get("items", [])
        if not items:
            return {"connected": False, "message": "No YouTube channel found."}
        channel = items[0]
        return {
            "connected": True,
            "channel": {
                "id": channel.get("id"),
                "title": channel.get("snippet", {}).get("title"),
                "description": channel.get("snippet", {}).get("description"),
                "statistics": channel.get("statistics", {}),
            },
        }
    except Exception as exc:
        return {"connected": False, "message": str(exc)}


@app.post("/api/youtube/upload")
async def upload(
    video: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    privacy: str = Form("private"),
    publish_at: str = Form(""),
    x_api_key: str | None = Header(default=None),
):
    require_api_key(x_api_key)
    if privacy not in {"private", "unlisted", "public"}:
        raise HTTPException(status_code=400, detail="invalid privacy")
    if not video.filename:
        raise HTTPException(status_code=400, detail="video file is required")

    credentials = load_credentials()
    if not credentials:
        raise HTTPException(status_code=401, detail="Connect YouTube OAuth before upload.")

    suffix = Path(video.filename).suffix or ".mp4"
    temp_path = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp_path = temp.name
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                temp.write(chunk)

        youtube = build("youtube", "v3", credentials=credentials)
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",
            },
            "status": {"privacyStatus": privacy},
        }

        if publish_at:
            body["status"]["publishAt"] = publish_at
            body["status"]["privacyStatus"] = "private"

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(
                temp_path,
                chunksize=8 * 1024 * 1024,
                resumable=True,
            ),
        )

        response = None
        while response is None:
            _, response = request.next_chunk()

        return {
            "ok": True,
            "video_id": response.get("id"),
            "url": (
                f"https://www.youtube.com/watch?v={response.get('id')}"
                if response.get("id")
                else None
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"YouTube upload failed: {exc}")
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
