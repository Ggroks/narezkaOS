# Narezka OS — образ для запуска сервисом.
#
# Две ступени: сборка интерфейса на Node и сам сервис на Python. Ставить Node
# в итоговый образ незачем — от фронтенда нужны только собранные файлы,
# а это лишние 300 МБ и лишняя поверхность для уязвимостей.

FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim

# ffmpeg — не зависимость языка, а внешний инструмент: им режется, кодируется
# и извлекается звук. Без него не работает ничего, поэтому он в образе,
# а не «поставьте сами».
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости отдельным слоем: правка кода не должна пересобирать их заново.
COPY pyproject.toml ./
COPY narezka/ ./narezka/
RUN pip install --no-cache-dir -e "."

COPY assets/ ./assets/
COPY configs/ ./configs/
COPY --from=frontend /build/dist ./frontend/dist

# Хранилище и веса моделей — на томах: они переживают пересборку образа.
# Веса распознавания качаются при первом запуске и весят сотни мегабайт;
# без тома это повторялось бы после каждого обновления.
VOLUME ["/data", "/root/.cache/huggingface"]
ENV NAREZKA_STORAGE=/data

EXPOSE 8000

# Слушает все адреса: снаружи контейнера иначе не достучаться. Наружу его
# всё равно выставляет обратный прокси или туннель, а не он сам.
CMD ["narezka", "serve", "--host", "0.0.0.0", "--port", "8000"]
