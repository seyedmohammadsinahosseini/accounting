# Accounting Platform — Production Deployment Guide

This guide describes production deployment options for **Accounting Platform**, including containerized deployment with Docker and bare-metal Debian/Ubuntu setups with hardened systemd units and Nginx.

---

## 1. Production Architecture Overview

```
Internet / Client Requests
          │
          ▼  HTTPS (Port 443)
┌─────────────────────────────────────────────────────────┐
|                  Nginx Reverse Proxy                    |
|  * TLS Termination (Let's Encrypt / Certbot)            |
|  * Security Headers (CSP, HSTS, X-Frame-Options)        |
|  * Static File Offloading / Gzip & Brotli Compression   |
└──────────────────────────┬──────────────────────────────┘
                           │  HTTP / Proxy Headers (Port 8000)
                           ▼
┌─────────────────────────────────────────────────────────┐
|               Accounting Platform ASGI                  |
|  * Uvicorn / FastAPI Backend                            |
|  * Running as unprivileged user: agency-accounting      |
|  * Sandboxed filesystem (ProtectSystem=strict)          |
└──────────────────────────┬──────────────────────────────┘
                           │  Local Disk (mode 0700)
                           ▼
┌─────────────────────────────────────────────────────────┐
|            Hardened Persistent Storage                  |
|  * SQLite WAL Database: /var/lib/agency-accounting/     |
|  * Cryptographic Secret Key: secret.key                 |
|  * Automated Daily Hot Snapshots: backups/              |
└─────────────────────────────────────────────────────────┘
```

---

## 2. Option A: Containerized Deployment (Docker Compose)

The fastest and most reproducible method to deploy Accounting Platform in production.

### Step 1: Clone Repository & Create Directory Structure
```bash
git clone https://github.com/seyedmohammadsinahosseini/Accounting-Platform.git
cd Accounting-Platform
mkdir -p data/backups data/logs
```

### Step 2: Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Ensure the following variables are configured:
```ini
ACCT_DEMO_SEED=0
ACCT_BOOTSTRAP_ADMIN_USERNAME=admin
ACCT_BOOTSTRAP_ADMIN_PASSWORD=SetAStrongProductionPassword987!
ACCT_ALLOW_PLAINTEXT_PII=0
ACCT_BACKUP_ENABLED=1
ACCT_BACKUP_EVERY_HOURS=24
ACCT_BACKUP_KEEP=30
```

### Step 3: Launch with Docker Compose
```bash
docker compose up -d --build
```

Verify service health:
```bash
docker compose ps
curl -f http://localhost:8000/api/health
```

---

## 3. Option B: Bare-Metal Linux Deployment (Debian / Ubuntu)

For bare-metal and standard virtual private servers (VPS), the repository provides hardened systemd units and Nginx configuration templates located in `deploy/debian/`.

### 3.1 Unprivileged System User Creation
```bash
sudo adduser --system --group --home /var/lib/agency-accounting --shell /usr/sbin/nologin agency-accounting
sudo install -d -o agency-accounting -g agency-accounting -m 0700 /var/lib/agency-accounting
sudo install -d -o root -g root -m 0755 /opt/agency-accounting
```

### 3.2 Application Installation
Clone the repository into `/opt/agency-accounting` and set up the Python virtual environment:
```bash
sudo git clone https://github.com/seyedmohammadsinahosseini/Accounting-Platform.git /opt/agency-accounting
cd /opt/agency-accounting
sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt
```

### 3.3 Configure Environment & Secrets
```bash
sudo install -o root -g agency-accounting -m 0640 \
  /opt/agency-accounting/deploy/debian/agency-accounting.env.example \
  /etc/agency-accounting.env
sudo nano /etc/agency-accounting.env
```
Key settings:
- `ACCT_DATA_DIR=/var/lib/agency-accounting`
- `ACCT_DEMO_SEED=0`
- `ACCT_BOOTSTRAP_ADMIN_USERNAME=admin`
- `ACCT_BOOTSTRAP_ADMIN_PASSWORD=ChooseStrongSecretPassword!`

### 3.4 Install Systemd Service Unit
The systemd service unit enforces OS-level sandboxing:
- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateTmp=true`
- `ReadWritePaths=/var/lib/agency-accounting`

```bash
sudo install -o root -g root -m 0644 \
  /opt/agency-accounting/deploy/debian/agency-accounting.service \
  /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now agency-accounting.service
sudo systemctl status agency-accounting.service
```

### 3.5 Automated Daily Backup Timer
```bash
sudo install -o root -g root -m 0644 \
  deploy/debian/agency-accounting-backup.service \
  /etc/systemd/system/

sudo install -o root -g root -m 0644 \
  deploy/debian/agency-accounting-backup.timer \
  /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now agency-accounting-backup.timer
```

### 3.6 Nginx Reverse Proxy Configuration
```bash
sudo cp deploy/debian/nginx-accounting.conf.example /etc/nginx/sites-available/accounting.conf
sudo ln -s /etc/nginx/sites-available/accounting.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Obtain an SSL certificate via Certbot:
```bash
sudo certbot --nginx -d your-accounting-domain.com
```

---

## 4. Disaster Recovery & Backup Restoration

To restore the system from a consistent snapshot:
1. Stop the active application service:
   ```bash
   sudo systemctl stop agency-accounting
   ```
2. Restore using the built-in CLI utility:
   ```bash
   python3 tools.py restore /var/lib/agency-accounting/backups/accounting-snapshot-20261018.db
   ```
3. Verify ledger balance and integrity:
   ```bash
   python3 tools.py check-balance
   ```
4. Restart the service:
   ```bash
   sudo systemctl start agency-accounting
   ```
