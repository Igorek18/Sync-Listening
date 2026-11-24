
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI
from redis.asyncio import Redis


# Подключение к Memurai
redis_client = Redis.from_url(
    "redis://localhost:6379/0",
    decode_responses=True
)


# Запуск и закрытие подключения к Redis
@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.ping()
    print("Memurai подключен!")

    yield

    await redis_client.aclose()


# Создание FastAPI
fastapi_app = FastAPI(lifespan=lifespan)


# Создание Socket.IO
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*"
)


# Главная страница
@fastapi_app.get("/")
async def home():
    return {
        "message": "Sync Listening is running!"
    }


# Проверка подключения к Redis
@fastapi_app.get("/health")
async def health():
    await redis_client.ping()

    return {
        "status": "ok",
        "redis": "connected"
    }


# Подключение пользователя
@sio.event
async def connect(sid, environ, auth):
    print(f"Пользователь подключился: {sid}")


# Отключение пользователя
@sio.event
async def disconnect(sid):
    print(f"Пользователь отключился: {sid}")


# Объединение FastAPI и Socket.IO
app = socketio.ASGIApp(
    sio,
    other_asgi_app=fastapi_app
)
