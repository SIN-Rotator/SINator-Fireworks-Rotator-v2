FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor xvfb x11vnc fluxbox \
    wget curl gnupg ca-certificates git \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install noVNC + websockify
RUN git clone https://github.com/novnc/noVNC.git /opt/noVNC && \
    ln -sf /opt/noVNC/vnc.html /opt/noVNC/index.html

# Install websockify as Python package + runner for noVNC
RUN pip install --no-cache-dir --break-system-packages websockify

# Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Local browser tools (sin_browser_tools for Fireworks signup flow)
COPY docker/sin-browser-tools/ /tmp/sin-browser-tools/
RUN pip install --no-cache-dir --break-system-packages /tmp/sin-browser-tools && \
    rm -rf /tmp/sin-browser-tools

# Install Playwright browsers
RUN playwright install chromium

# Chrome profile with GMX session cookies (from OCI VM)
COPY docker/chrome-profile-essential.tar.gz /tmp/chrome-profile.tar.gz
RUN mkdir -p /chrome-profile && \
    tar xzf /tmp/chrome-profile.tar.gz -C /chrome-profile && \
    rm /tmp/chrome-profile.tar.gz && \
    chmod -R 700 /chrome-profile

# Rotator code
WORKDIR /app
COPY agent_toolbox/ /app/agent_toolbox/
COPY tools/ /app/tools/
COPY config/ /app/config/
COPY *.md /app/

# Pool file
VOLUME /app/data

# Supervisor config
COPY docker/supervisord.conf /etc/supervisord.conf

ENV DISPLAY=:1
ENV SIN_DOCKER=1
ENV SIN_DOCKER_PROFILE=/chrome-profile

EXPOSE 5900 6080

CMD ["supervisord", "-c", "/etc/supervisord.conf", "-n"]
