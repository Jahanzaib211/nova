{{/*
Common labels applied to every resource in this chart.
*/}}
{{- define "nova.labels" -}}
app.kubernetes.io/part-of: nova
environment: staging
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{/*
Selector labels for a given component (e.g. "gateway", "frontend").
Usage: {{ include "nova.selectorLabels" (dict "component" "gateway") }}
*/}}
{{- define "nova.selectorLabels" -}}
app.kubernetes.io/name: nova
app.kubernetes.io/component: {{ .component }}
{{- end -}}
