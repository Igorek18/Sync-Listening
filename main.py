
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis


# Папки проекта
BASE_DIR = Path(__file__).resolve().parent
MUSIC_DIR = BASE_DIR / "music"
STATIC_DIR = BASE_DIR / "static"

MUSIC_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Подключение к Memurai
redis_client = Redis.from_url(
    "redis://localhost:6379/0",
    decode_responses=True
)

TRACKS_KEY = "tracks:list"
MAX_FILE_SIZE = 30 * 1024 * 1024


# Запуск и остановка приложения
@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.ping()
    print("Memurai подключен!")
    yield
    await redis_client.aclose()


fastapi_app = FastAPI(lifespan=lifespan)


# Проверка сервера
@fastapi_app.get("/api/health")
async def health():
    await redis_client.ping()
    return {"status": "ok", "redis": "connected"}


# Загрузка MP3
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

    title = Path(file.filename).stem

    try:
        file_path.write_bytes(content)

        track = {
            "id": track_id,
            "title": title,
            "filename": filename,
            "url": f"/music/{filename}"
        }

        await redis_client.hset(
            f"track:{track_id}",
            mapping=track
        )
        await redis_client.rpush(TRACKS_KEY, track_id)

    except Exception:
        file_path.unlink(missing_ok=True)
        raise HTTPException(500, "Не удалось сохранить композицию")

    return {
        "message": "Композиция загружена",
        "track": track
    }


# Получение списка композиций
@fastapi_app.get("/api/tracks")
async def get_tracks():
    track_ids = await redis_client.lrange(TRACKS_KEY, 0, -1)

    tracks = []

    for track_id in track_ids:
        track = await redis_client.hgetall(f"track:{track_id}")

        if track:
            tracks.append(track)

    return tracks


# Socket.IO
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*"
)


@sio.event
async def connect(sid, environ, auth):
    print(f"Пользователь подключился: {sid}")


@sio.event
async def disconnect(sid):
    print(f"Пользователь отключился: {sid}")


# Раздача музыкальных файлов и интерфейса
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


# Общее ASGI-приложение
app = socketio.ASGIApp(
    sio,
    other_asgi_app=fastapi_app
)
