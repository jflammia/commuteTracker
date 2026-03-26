FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/

ENV DATABASE_URL=sqlite:///data/commute_tracker.db
ENV RECEIVER_HOST=0.0.0.0
ENV RECEIVER_PORT=8080

EXPOSE 8080

VOLUME ["/data"]

CMD ["uvicorn", "src.receiver.app:app", "--host", "0.0.0.0", "--port", "8080"]
