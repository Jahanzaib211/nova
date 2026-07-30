#!/usr/bin/env bash
# Build Nova's gateway/frontend images and import them into k3s's containerd
# store. Single-node k3s uses containerd, not the Docker daemon — there's no
# registry in this setup, so `docker save | k3s ctr images import` is how a
# locally-built image becomes schedulable.
#
# The `k3s ctr images import` step needs root (the containerd socket is
# root:root, srw-rw----) — run this script with sudo, or run the docker
# build lines yourself and the import lines separately with sudo.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root
TAG="${1:-staging}"

echo "== Building nova-gateway:${TAG} (default/final Dockerfile stage — non-root, no --reload) =="
docker build -f backend/Dockerfile -t nova-gateway:"$TAG" .

echo "== Building nova-frontend:${TAG} (prod target) =="
docker build -f frontend/Dockerfile --target prod -t nova-frontend:"$TAG" .

echo "== Building nova-provisioner:${TAG} (the provisioner service's own image) =="
docker build -t nova-provisioner:"$TAG" docker/provisioner/

echo "== Importing images into k3s containerd (needs root) =="
for img in nova-gateway:"$TAG" nova-frontend:"$TAG" nova-provisioner:"$TAG" nova-sandbox-android:latest; do
  echo "-- importing $img --"
  docker save "$img" | k3s ctr images import -
done

echo "== Verifying =="
k3s ctr images ls | grep -E 'nova-gateway|nova-frontend|nova-provisioner|nova-sandbox-android'
