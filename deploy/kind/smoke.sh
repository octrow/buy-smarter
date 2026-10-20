#!/usr/bin/env bash
# kind smoke test for the Helm chart: Postgres + Redis, install (migrations run as a hook), helm test, probes,
# a recommend job through API → Redis → worker, upgrade.
# Expects a kind cluster and the image already loaded into it: IMAGE=repo TAG=tag deploy/kind/smoke.sh
set -euo pipefail
IMAGE=${IMAGE:-ghcr.io/octrow/buy-smarter}
TAG=${TAG:-ci}
NS=buysmarter
CHART=deploy/helm/buysmarter
DB_URL=postgresql+asyncpg://buysmarter:buysmarter@postgres.db.svc.cluster.local:5432/buysmarter
REDIS_URL=redis://redis.db.svc.cluster.local:6379/0
JOB_TIMEOUT_S=60

dump() {
  kubectl get pods -A -o wide || true
  kubectl -n "$NS" describe pods || true
  kubectl -n "$NS" logs -l app.kubernetes.io/instance=buysmarter --all-containers --tail=200 || true
}
trap 'dump' ERR

kubectl apply -f deploy/kind/deps.yaml
kubectl -n db rollout status deploy/postgres --timeout=180s
kubectl -n db rollout status deploy/redis --timeout=120s

install() {
  helm upgrade --install buysmarter "$CHART" -n "$NS" \
    --set image.repository="$IMAGE" --set image.tag="$TAG" \
    --set secret.data.DATABASE_URL="$DB_URL" --set secret.data.REDIS_URL="$REDIS_URL" \
    --set env.BUYSMARTER_JOB_TIMEOUT_S="$JOB_TIMEOUT_S" "$@" \
    --wait --timeout 3m
}
install
helm test buysmarter -n "$NS" --logs

kubectl -n "$NS" port-forward svc/buysmarter 8000:8000 >/dev/null 2>&1 &
trap 'kill %1 2>/dev/null || true' EXIT
for _ in $(seq 20); do curl -fsS localhost:8000/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS localhost:8000/health
curl -fsS localhost:8000/ready
echo
curl -fsS localhost:8000/openapi.json >/dev/null
curl -fsS localhost:8000/metrics | grep -q 'http_server_request_duration_seconds_count{.*http_route="/openapi.json"'
echo "metrics: ok"

# A job goes API → Redis → worker → terminal status. CI egress may be refused by the marketplaces,
# so any terminal status counts except an internal failure: this checks the wiring, not the ranking.
job=$(curl -fsS -X POST localhost:8000/recommend -H 'content-type: application/json' \
  -d '{"query": "ирригатор до 40000", "sources": ["olx"]}')
status_url=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["status_url"])' "$job")
for _ in $(seq $((JOB_TIMEOUT_S + 60))); do
  view=$(curl -fsS "localhost:8000$status_url")
  status=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["status"])' "$view")
  case $status in done|no_match|failed) break ;; esac
  sleep 1
done
echo "job: $status $(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("error") or "")' "$view")"
case $status in done|no_match) ;; failed) ! grep -q '"error":"internal"' <<<"$view" ;; *) false ;; esac

# Upgrade: the migration hook runs again (no-op) and the pods roll.
install --set podAnnotations.smoke=upgrade
helm test buysmarter -n "$NS"
kubectl -n "$NS" exec deploy/buysmarter -- buysmarter doctor | grep -E '^Storage +ok'
echo "kind smoke: ok"
