FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NUM_SOLVER_WORKERS=2 \
    ALLOWED_ORIGIN=http://localhost:5173 \
    PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY analysis/ analysis/
COPY data/legs_frame.pkl data/airport_nodes.pkl data/

EXPOSE 8080

CMD exec uvicorn backend.main:app --host 0.0.0.0 --port $PORT
