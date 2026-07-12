# Runbook: Disk >90%

## Symptoms
Alert fires: "Disk usage >90% on mount"
Metric: `(1 - node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100 > 90`

## What it means
One of your mount points has less than 10% free space.

## Identify the problem

```bash
# See all mounts and usage
df -h

# See which mounts are critical
# Check root, /home, /var, /tmp especially

# Largest directories in /home
sudo du -sh /home/* 2>/dev/null | sort -rh | head -10

# Check Docker disk usage
docker system df
```

## Common causes on this box

1. **Loki/Prometheus data growth** — the `loki-data` and `prometheus-data` Docker volumes.
   ```bash
   # Check Loki size
   docker exec nova-loki du -sh /loki
   # Check Prometheus size
   docker exec nova-prometheus du -sh /prometheus

   # Reduce retention if needed (edit loki.yaml / prometheus.yml then restart)
   ```

2. **Docker log overflow** — containers producing excessive logs.
   ```bash
   docker logs --tail 100 <container-name>  # see recent logs
   # Set log rotation for a container:
   # docker update --log-opt max-size=10m --log-opt max-file=3 <container-name>
   ```

3. **Home directory full** — ML model files, datasets, etc.
   ```bash
   du -sh ~/.cache/huggingface 2>/dev/null  # HF cache
   du -sh ~/.llama* 2>/dev/null              # llama.cpp models
   du -sh /home/jahanzaib/Desktop/* 2>/dev/null | sort -rh | head -5
   ```

## Immediate relief

```bash
# Clean Docker build cache
docker system prune -af

# Clean apt cache
sudo apt autoremove -y
sudo apt clean

# Rotate PM2 logs (they can grow large)
pm2 flush

# Prune visitor log (30-day retention)
python3 /home/jahanzaib/Desktop/nova/scripts/nova-visitors.py --prune 30
```

## Long-term fix

1. Add a disk usage alert at 80% as a warning threshold (before it becomes critical)
2. Set up automated cleanup cron (Docker prune weekly, log rotation)
3. Move large datasets to an external mount
