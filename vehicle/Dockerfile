FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir eclipse-zenoh

COPY vehicle/vehicle.py .
COPY vehicle/cam_builder.py .
COPY vehicle/mcm_builder.py .
COPY vehicle/comms.py .
COPY vehicle/geo.py .
COPY vehicle/simulation.py .
COPY vehicle/protocol.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "vehicle.py"]
