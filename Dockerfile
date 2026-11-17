# Para servicios gratuitos basados en Docker (Koyeb, Hugging Face Spaces, Fly...).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=7860
EXPOSE 7860
CMD gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT
