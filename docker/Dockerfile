
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --quiet -r requirements.txt
COPY . .
RUN mkdir -p data logs
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8080"]
