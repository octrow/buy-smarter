{{- define "buysmarter.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "buysmarter.fullname" -}}
{{- if contains .Chart.Name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "buysmarter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "buysmarter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "buysmarter.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "buysmarter.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "buysmarter.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}
{{- end }}

{{- define "buysmarter.secretName" -}}
{{- .Values.secret.existing | default (include "buysmarter.fullname" .) }}
{{- end }}

{{/* Pod Security Standard "restricted"; uid 10001 is the image's user. */}}
{{- define "buysmarter.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "buysmarter.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "buysmarter.envFrom" -}}
{{- $env := deepCopy .Values.env }}
{{- if not .Values.metrics.enabled }}
{{- $_ := set $env "BUYSMARTER_METRICS" "0" }}
{{- end }}
{{- with .Values.otel.endpoint }}
{{- $_ := set $env "OTEL_EXPORTER_OTLP_ENDPOINT" . }}
{{- end }}
envFrom:
  - secretRef:
      name: {{ include "buysmarter.secretName" . }}
{{- with $env }}
env:
  {{- range $k, $v := . }}
  - name: {{ $k }}
    value: {{ $v | quote }}
  {{- end }}
{{- end }}
{{- end }}
