# Deployment Guide

Production setup for pxx and optional services.

> The `agentmemory` and `9router` services are experimental and source-only
> (not on PyPI). Their pxx integration is partially wired: this guide says
> explicitly what happens today and what is not yet connected.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│           Your Project Directory                    │
│  (runs `pxx --edit --with-memory`)                  │
└──────────┬──────────────────────────────────────────┘
           │
           ├──────────────────┬────────────────────────┐
           │                  │                        │
           ▼                  ▼                        ▼
    ┌─────────────┐   ┌─────────────────┐    ┌─────────────────┐
    │   aider     │   │  agentmemory    │    │ 9router         │
    │  (frontend) │   │  (observation   │    │ (optional       │
    └─────────────┘   │   storage &     │    │  single-upstream│
           │          │   search)       │    │  proxy)         │
           │          └─────────────────┘    └─────────────────┘
           │                  ▲                        │
           │                  │                        │
           └──────────────────┼────────────────────────┘
                      │       │ post-session git-diff
                      ▼       │ summary (pxx → service)
             ┌──────────────────┐
             │ Ollama (server)  │
             │ (LLM inference)  │
             └──────────────────┘
```

Automatic injection of observations *into* the aider session is **not wired**
in this release; retrieval is explicit via the service API.

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
2. pxx starts the agentmemory service (background)
3. pxx launches aider subprocess
4. On aider exit, pxx stores a post-session edit summary (git-diff based)

9router starts only with `--with-router`. Automatic injection of observations
into the aider session is not wired in this release.

**Configuration (optional):**
```bash
# 30-day observation retention
export AGENTMEMORY_RETENTION_DAYS=30

# Cleanup every 30 minutes
export AGENTMEMORY_CLEANUP_INTERVAL=1800

# Run pxx
pxx --edit --with-memory
```

## Two-Machine Deployment (LAN)

**Inference host (Ollama)** ← **Orchestrator host (pxx + agentmemory)**

### Inference host setup

Only Ollama needs to live here:

```bash
ollama serve
```

### Orchestrator host setup

```bash
# 1. Install pxx
pip install pxx-orchestrator

# 2. Configure the inference-host endpoint
export PXX_STUDIO_LAN_URL=http://your-ollama-host:11434

# 3. Run pxx
pxx --edit
```

> **Memory is loopback-only in this release.** pxx talks to agentmemory on
> the fixed address `127.0.0.1:3111` — a remote memory service is not
> configurable. If you want `--with-memory`, run agentmemory on the
> orchestrator host (install from a repo checkout: `pip install -e
> services/agentmemory`, then `agentmemory`).

**What happens:**
1. pxx detects the inference host's Ollama endpoint (LAN)
2. aider runs on the orchestrator host, using the inference host's Ollama
3. With `--with-memory`, a post-session edit summary is stored on the
   orchestrator host's local agentmemory

## VPN Deployment (Remote)

**Inference host (LAN)** ← **VPN** ← **Orchestrator host (remote)**

Same as two-machine, but use the VPN hostname:

```bash
# On the remote orchestrator host, over VPN
export PXX_STUDIO_REMOTE_URL=https://workstation-vpn.example.com:11434

pxx --edit
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
Environment=PXX_MEMORY_HOST=127.0.0.1
Environment=PXX_MEMORY_PORT=3111
ExecStart=/home/your-username/.local/bin/agentmemory
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

CMD ["agentmemory"]
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

**Larger datasets:**
```bash
# Increase cleanup interval if cleanup competes with active requests
export AGENTMEMORY_CLEANUP_INTERVAL=7200  # Every 2 hours

# Run pxx
pxx --edit --with-memory
```

`AGENTMEMORY_SEARCH_LIMIT`, `AGENTMEMORY_USE_VECTOR_INDEX`, and
`AGENTMEMORY_CACHE_SIZE` are not implemented runtime settings. The HNSW index
is currently experimental and is not populated by the production observation
path. Do not use the service's current tests as evidence for a 100k latency or
recall guarantee.

## Troubleshooting

**agentmemory not starting:**
```bash
# Check port availability
lsof -i :3111

# Run the service on a different port
PXX_MEMORY_PORT=3112 agentmemory
# Note: pxx always talks to agentmemory on 127.0.0.1:3111 in this release,
# so a custom port only helps for standalone service use.
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
# Development (safe — local only; this is the default)
agentmemory                                     # binds 127.0.0.1:3111

# Production on LAN (add firewall rules)
PXX_MEMORY_HOST=0.0.0.0 agentmemory             # ⚠️ Needs firewall

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
