#!/usr/bin/env bash
# kind smoke test for the Helm chart: Postgres, install (migrations run as a hook), helm test, probes, upgrade.
# Expects a kind cluster and the image already loaded into it: IMAGE=repo TAG=tag deploy/kind/smoke.sh
set -euo pipefail
IMAGE=${IMAGE:-ghcr.io/octrow/buy-smarter}
TAG=${TAG:-ci}
NS=buysmarter
CHART=deploy/helm/buysmarter
DB_URL=postgresql+asyncpg://buysmarter:buysmarter@postgres.db.svc.cluster.local:5432/buysmarter

dump() {
  kubectl get pods -A -o wide || true
  kubectl -n "$NS" describe pods || true
  kubectl -n "$NS" logs -l app.kubernetes.io/instance=buysmarter --all-containers --tail=200 || true
}
trap 'dump' ERR

kubectl apply -f deploy/kind/postgres.yaml
kubectl -n db rollout status deploy/postgres --timeout=180s

install() {
  helm upgrade --install buysmarter "$CHART" -n "$NS" \
    --set image.repository="$IMAGE" --set image.tag="$TAG" \
    --set secret.data.DATABASE_URL="$DB_URL" "$@" \
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

# Upgrade: the migration hook runs again (no-op) and the pods roll.
install --set podAnnotations.smoke=upgrade
helm test buysmarter -n "$NS"
kubectl -n "$NS" exec deploy/buysmarter -- buysmarter doctor | grep -E '^Postgres +ok'
echo "kind smoke: ok"
