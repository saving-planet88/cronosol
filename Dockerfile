FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Railway inyecta $PORT en tiempo de ejecución; en local usa 8000 por defecto
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
