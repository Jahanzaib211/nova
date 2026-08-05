{{/*
Common labels applied to every resource in this chart.
*/}}
{{- define "nova.labels" -}}
app.kubernetes.io/part-of: nova
environment: staging
{{/*
  `replace "+" "_"` is not cosmetic and must not be dropped. A chart version is
  SemVer, so it may carry build metadata after a `+` — and with the HelmRelease
  on `reconcileStrategy: Revision`, Flux *always* appends the git SHA that way:
  `nova-0.1.0+d6c5e8a328a6`. `+` is not a legal character in a Kubernetes label
  value, so every single object in the chart failed server-side apply with
  "metadata.labels: Invalid value", the upgrade rolled back, and the release was
  wedged. Helm's own `helm create` scaffold carries this replace for exactly
  this reason; this chart was hand-written and omitted it.

  trunc 63 because label values are capped at 63 characters.
*/}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{/*
Selector labels for a given component (e.g. "gateway", "frontend").
Usage: {{ include "nova.selectorLabels" (dict "component" "gateway") }}
*/}}
{{- define "nova.selectorLabels" -}}
app.kubernetes.io/name: nova
app.kubernetes.io/component: {{ .component }}
{{- end -}}
