FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir eclipse-zenoh

COPY vehicle/vehicle.py .
COPY vehicle/cam_builder.py .
COPY vehicle/mcm_builder.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "vehicle.py"]
