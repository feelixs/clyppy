# Use Python 3.12.6 as a parent image
FROM python:3.12.6-slim

LABEL version="1.0"
LABEL description="This is a custom Python image for my application."
WORKDIR /app

# Create data directory for persistent storage
RUN mkdir -p /app/data
COPY requirements.txt /app
RUN apt-get -yq update && apt-get install -y git
RUN apt-get install -yq ffmpeg curl unzip
RUN pip3 install --no-cache-dir -r requirements.txt

# Install Deno for yt-dlp YouTube n-challenge solving
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

COPY . /app
COPY .env* ./

CMD ["python3", "main.py"]
