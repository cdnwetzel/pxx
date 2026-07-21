# Deployment Guide

Production setup for pxx and optional services.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│           Your Project Directory                    │
│  (runs `pxx --edit --with-memory`)                  │
└──────────┬──────────────────────────────────────────┘
           │
           ├─────────────────────────────────────────────┐
           │                                             │
           ▼                                             ▼
    ┌─────────────┐                          ┌─────────────────┐
    │   aider     │                          │  agentmemory    │
    │  (frontend) │◄─────────────────────────┤  (observation   │
    │             │  /inject (inject         │   storage &     │
    └─────────────┘   observations)          │   search)       │
                                             └─────────────────┘
           │                                             │
           └─────────────────────────────────────────────┘
                    │
                    ▼
           ┌──────────────────┐
           │ Ollama (server)  │
           │ (LLM inference)  │
           └──────────────────┘
```

## Single-Machine Deployment (Local)

All services on one machine. Default pxx behavior.

### Setup

```bash
# 1. Install pxx (core). Optional services are source-installed — see INSTALL.md
pip install pxx-orchestrator

# 2. Start Ollama (if not already running)
ollama serve

# 3. In another terminal, run pxx with memory
pxx --edit --with-memory
```

**What happens automatically:**
1. pxx detects Ollama endpoint
2. pxx starts agentmemory service (background)
3. pxx starts 9router service (background)
4. pxx launches aider subprocess
5. aider can inject observations from agentmemory
6. On aider exit, pxx captures tool calls as observations

**Configuration (optional):**
```bash
# 30-minute observation retention
export AGENTMEMORY_RETENTION_DAYS=30

# Cleanup every 30 minutes
export AGENTMEMORY_CLEANUP_INTERVAL=1800

# Run pxx
pxx --edit --with-memory
```

## Two-Machine Deployment (LAN)

**Inference host (Ollama + agentmemory)** ← **Orchestrator host (pxx)**

### Inference host setup

```bash
# 1. Install the agentmemory service from a repo checkout (not on PyPI)
git clone https://github.com/cdnwetzel/pxx && cd pxx
pip install -e services/agentmemory

# 2. Start agentmemory service
agentmemory server --port 3111 &

# 3. Verify it's running
curl http://127.0.0.1:3111/health
```

### Orchestrator host setup

```bash
# 1. Install pxx
pip install pxx-orchestrator

# 2. Configure the inference-host endpoint
export PXX_STUDIO_LAN_URL=http://your-ollama-host:11434
export AGENTMEMORY_URL=http://your-ollama-host:3111

# 3. Run pxx with memory pointing at the inference host
pxx --edit --with-memory
```

**What happens:**
1. pxx detects the inference host's Ollama endpoint (LAN)
2. pxx queries its agentmemory service for observations
3. aider runs on the orchestrator host, using the inference host's Ollama
4. Tool calls captured and sent back to the inference host's agentmemory

## VPN Deployment (Remote)

**Inference host (LAN)** ← **VPN** ← **Orchestrator host (remote)**

Same as two-machine, but use the VPN hostname:

```bash
# On the remote orchestrator host, over VPN
export PXX_STUDIO_REMOTE_URL=https://workstation-vpn.example.com:11434
export AGENTMEMORY_URL=https://workstation-vpn.example.com:3111

pxx --edit --with-memory
```

## Systemd Integration (Linux/WSL)

Auto-start agentmemory service on boot.

```bash
# Create systemd unit for agentmemory
sudo tee /etc/systemd/system/agentmemory.service << 'EOF'
[Unit]
Description=agentmemory observation service
After=network.target

[Service]
Type=simple
User=your-username
ExecStart=/home/your-username/.local/bin/agentmemory server --port 3111
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl enable agentmemory.service
sudo systemctl start agentmemory.service

# Check status
sudo systemctl status agentmemory.service

# View logs
sudo journalctl -u agentmemory.service -f
```

## Docker Deployment (Optional)

For containerized agentmemory service:

```dockerfile
# Dockerfile.agentmemory
FROM python:3.12-slim

WORKDIR /app

# agentmemory ships from this repo, not PyPI — copy the service in and install it
COPY services/agentmemory /app/agentmemory
RUN pip install /app/agentmemory

EXPOSE 3111

CMD ["agentmemory", "server", "--port", "3111"]
```

**Build and run:**
```bash
docker build -f Dockerfile.agentmemory -t agentmemory:latest .
docker run -d \
  --name agentmemory \
  -p 3111:3111 \
  -v ~/.pxx/memory-archive:/root/.pxx/memory-archive \
  agentmemory:latest
```

## Monitoring & Maintenance

### Health Checks

```bash
# Check pxx endpoints
pxx --list-commands

# Check agentmemory service
curl http://127.0.0.1:3111/health

# Check Ollama endpoint
curl http://127.0.0.1:11434/api/tags

# Check 9router (if using --with-router)
curl http://127.0.0.1:20128/health
```

### Storage Management

**Check archive size:**
```bash
du -sh ~/.pxx/memory-archive/
```

**Cleanup old archives (manual):**
```bash
# Delete archives older than 3 months
find ~/.pxx/memory-archive -type f -mtime +90 -delete
```

**Automatic cleanup (via API):**
```bash
# Trigger cleanup with default retention
curl -X POST http://127.0.0.1:3111/cleanup \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'
```

### Performance Tuning

**Large datasets (>100k observations):**
```bash
# Reduce search limit (faster queries)
export AGENTMEMORY_SEARCH_LIMIT=5

# Increase cleanup interval (less overhead)
export AGENTMEMORY_CLEANUP_INTERVAL=7200  # Every 2 hours

# Run pxx
pxx --edit --with-memory
```

**Memory-constrained systems:**
```bash
# Disable HNSW vector index (use brute-force)
export AGENTMEMORY_USE_VECTOR_INDEX=false

# Reduce cache size
export AGENTMEMORY_CACHE_SIZE=32

# Run pxx
pxx --edit --with-memory
```

## Troubleshooting

**agentmemory not starting:**
```bash
# Check port availability
lsof -i :3111

# Try alternate port
agentmemory server --port 3112
export AGENTMEMORY_URL=http://127.0.0.1:3112
```

**Connection timeout to the inference host:**
```bash
# Check network connectivity
ping your-ollama-host

# Check firewall
sudo ufw allow 11434
sudo ufw allow 3111
```

**Disk space issues:**
```bash
# Check archive disk usage
du -sh ~/.pxx/memory-archive/

# Delete old archives
rm -rf ~/.pxx/memory-archive/2025-*

# Or via API: trigger cleanup with dry-run first
curl "http://127.0.0.1:3111/cleanup?dry_run=true"
```

## Backup & Restore

**Backup observations:**
```bash
# Archive all data
tar -czf ~/pxx-backup-$(date +%Y%m%d).tar.gz ~/.pxx/

# Or just memory database
cp ~/.pxx/memory.db ~/pxx-memory-backup.db
```

**Restore observations:**
```bash
# Restore from tar
tar -xzf ~/pxx-backup-20260604.tar.gz -C ~/

# Or restore database
cp ~/pxx-memory-backup.db ~/.pxx/memory.db
```

## Security Notes

⚠️ **Important:** agentmemory does not authenticate requests. Only expose on trusted networks.

```bash
# Development (safe — local only)
agentmemory server --host 127.0.0.1 --port 3111  # ✓

# Production on LAN (add firewall rules)
agentmemory server --host 0.0.0.0 --port 3111    # ⚠️ Needs firewall

# Never expose to the internet directly — front with an authenticating reverse proxy or VPN
```

**Firewall example (ufw):**
```bash
# Allow only from LAN subnet
sudo ufw allow from <your-lan-cidr> to any port 3111
```

## Next Steps

1. **Choose deployment model** (local / LAN / VPN)
2. **Follow setup instructions** for your environment
3. **Run health checks** to verify connectivity
4. **Test with example workflow:** `docs/EXAMPLES.md`
5. **Set retention policy** for your use case

## Support

- **Deployment issues:** https://github.com/cdnwetzel/pxx/issues
- **Configuration help:** Check environment variables in `INSTALL.md`
- **Architecture questions:** See `CLAUDE.md` for system design
