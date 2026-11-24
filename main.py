
import uuid
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime

import socketio
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis


BASE_DIR = Path(__file__).resolve().parent
MUSIC_DIR = BASE_DIR / "music"
STATIC_DIR = BASE_DIR / "static"

MUSIC_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

redis_client = Redis.from_url(
    "redis://localhost:6379/0",
    decode_responses=True
)

TRACKS_KEY = "tracks:list"
MAX_FILE_SIZE = 30 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.ping()
    print("Memurai подключен!")
    yield
    await redis_client.aclose()


fastapi_app = FastAPI(lifespan=lifespan)


# Проверка подключения
@fastapi_app.get("/api/health")
async def health():
    await redis_client.ping()
    return {"status": "ok", "redis": "connected"}


# Загрузка музыки
@fastapi_app.post("/api/tracks")
async def upload_track(file: UploadFile = File(...)):
    if not file.filename or Path(file.filename).suffix.lower() != ".mp3":
        raise HTTPException(400, "Можно загружать только MP3-файлы")

    content = await file.read(MAX_FILE_SIZE + 1)

    if not content:
        raise HTTPException(400, "Файл пустой")

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "Максимальный размер файла — 30 МБ")

    track_id = str(uuid.uuid4())
    filename = f"{track_id}.mp3"
    file_path = MUSIC_DIR / filename

    track = {
        "id": track_id,
        "title": Path(file.filename).stem,
        "filename": filename,
        "url": f"/music/{filename}"
    }

    try:
        file_path.write_bytes(content)

        await redis_client.hset(
            f"track:{track_id}",
            mapping=track
        )
        await redis_client.rpush(TRACKS_KEY, track_id)

    except Exception:
        file_path.unlink(missing_ok=True)
        raise HTTPException(500, "Не удалось сохранить композицию")

    return {"message": "Композиция загружена", "track": track}


# Получение библиотеки
@fastapi_app.get("/api/tracks")
async def get_tracks():
    track_ids = await redis_client.lrange(TRACKS_KEY, 0, -1)
    tracks = []

    for track_id in track_ids:
        track = await redis_client.hgetall(f"track:{track_id}")
        if track:
            tracks.append(track)

    return tracks


# Создание комнаты
@fastapi_app.post("/api/rooms")
async def create_room():
    while True:
        code = secrets.token_hex(3).upper()
        if not await redis_client.exists(f"room:{code}"):
            break

    await redis_client.hset(
        f"room:{code}",
        mapping={
            "code": code,
            "created_at": datetime.now().isoformat(),
            "track_url": "",
            "track_title": "",
            "playing": "false",
            "position": "0"
        }
    )

    return {
        "message": "Комната создана",
        "code": code
    }


# Проверка комнаты
@fastapi_app.get("/api/rooms/{code}")
async def get_room(code: str):
    code = code.upper()
    room = await redis_client.hgetall(f"room:{code}")

    if not room:
        raise HTTPException(404, "Комната не найдена")

    return room


# Socket.IO
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*"
)


async def send_room_users(code):
    members = await redis_client.hgetall(f"room:{code}:members")

    await sio.emit(
        "room_users",
        list(members.values()),
        room=code
    )


@sio.event
async def connect(sid, environ, auth):
    print(f"Пользователь подключился: {sid}")


@sio.event
async def join_room(sid, data):
    code = str(data.get("code", "")).upper()
    username = str(data.get("username", "Гость")).strip()[:30]

    if not code or not await redis_client.exists(f"room:{code}"):
        await sio.emit(
            "room_error",
            {"message": "Комната не найдена"},
            to=sid
        )
        return

    old_room = await redis_client.get(f"sid:room:{sid}")

    if old_room:
        await sio.leave_room(sid, old_room)
        await redis_client.hdel(f"room:{old_room}:members", sid)
        await send_room_users(old_room)

    await sio.enter_room(sid, code)

    await redis_client.hset(
        f"room:{code}:members",
        sid,
        username or "Гость"
    )

    await redis_client.set(f"sid:room:{sid}", code)

    room_state = await redis_client.hgetall(f"room:{code}")

    await sio.emit(
        "room_joined",
        {
            "code": code,
            "username": username or "Гость",
            "state": room_state
        },
        to=sid
    )

    await send_room_users(code)

    print(f"{username} подключился к комнате {code}")


@sio.event
async def leave_room(sid):
    code = await redis_client.get(f"sid:room:{sid}")

    if code:
        await sio.leave_room(sid, code)
        await redis_client.hdel(f"room:{code}:members", sid)
        await redis_client.delete(f"sid:room:{sid}")
        await send_room_users(code)


@sio.event
async def disconnect(sid):
    code = await redis_client.get(f"sid:room:{sid}")

    if code:
        await redis_client.hdel(f"room:{code}:members", sid)
        await redis_client.delete(f"sid:room:{sid}")
        await send_room_users(code)

    print(f"Пользователь отключился: {sid}")


# Раздача файлов
fastapi_app.mount(
    "/music",
    StaticFiles(directory=str(MUSIC_DIR)),
    name="music"
)

fastapi_app.mount(
    "/",
    StaticFiles(directory=str(STATIC_DIR), html=True),
    name="static"
)


app = socketio.ASGIApp(
    sio,
    other_asgi_app=fastapi_app
)
