FROM python:3.12-slim
RUN pip install fastapi uvicorn httpx --no-cache
COPY main.py .
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
