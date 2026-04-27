FROM ghcr.io/nap-it/vanetza-nap:latest

# Instala Python 3 e pip (a imagem base é Debian mas não inclui Python)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Pré-instala zenoh para não precisar de o fazer em runtime
RUN pip3 install eclipse-zenoh
