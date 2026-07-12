# Runbook: Prometheus is down

## Symptoms
Alert fires: "Prometheus is DOWN — monitoring stack is blind"
Metric: `up{job="prometheus"} == 0`

## What it means
The `nova-prometheus` Docker container has stopped.
All metrics scraping ceases. Dashboards will show "No data".
The Grafana alerting engine stops evaluating alert rules.

**This is a metadata failure**: the monitored services (nginx, gateway, etc.)
may still be running fine — Prometheus just can't see them.

## Immediate fix

```bash
# Check the container status
docker ps -a | grep prometheus

# Restart via PM2
pm2 restart nova-monitoring
# or directly:
docker compose -f docker/monitoring/docker-compose.yaml restart prometheus

# Check logs
docker logs nova-prometheus --tail 30
pm2 logs nova-monitoring --err --lines 50
```

## If the container won't start

```bash
# Check why Prometheus won't start
docker run --rm -it \
  -v $(pwd)/docker/monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
  prom/prometheus:v3.4.1 \
  --check-config

# Common config errors:
# - syntax error in prometheus.yml
# - targets unreachable (all loopback but docker network misconfigured)
```

## Verify recovery

```bash
# Prometheus should be up
curl -s http://localhost:9090/-/healthy

# Targets should all be discovered
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys,json
d=json.load(sys.stdin)
for t in d['data']['activeTargets']:
    print(f'{t[\"labels\"][\"job\"]:20} {t[\"health\"]}')"

# Dashboards should repopulate (takes ~30s)
open http://localhost:3002
```

## If the data directory is corrupted

Prometheus stores its TSDB in the `prometheus-data` Docker volume.
If the volume is corrupted, you will lose historical metrics.

```bash
# Inspect the volume
docker volume inspect nova-prometheus_prometheus-data

# To reset (loses all historical metrics):
docker compose -f docker/monitoring/docker-compose.yaml down
docker volume rm nova-prometheus_prometheus-data
make monitoring-up
```

## Prevention

- Don't kill the container with `docker kill` — use `docker stop` or PM2 restart
- The `restart: unless-stopped` policy in docker-compose.yaml handles most crashes
- Check disk space regularly (see "Disk >90%" runbook) — out-of-disk is the most common cause
