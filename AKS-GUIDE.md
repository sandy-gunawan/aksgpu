# AKS for Beginners — Learn Kubernetes Through Weather Prediction

> **What you'll learn**: How Azure Kubernetes Service works, by building and running a real GPU-powered weather prediction app. Every concept explained with our actual running system as the example.

---

## Table of Contents

1. [What is AKS? (The 5-Minute Version)](#1-what-is-aks)
2. [Nodes and Node Pools — The Machines](#2-nodes-and-node-pools)
3. [Pods — Where Your Code Runs](#3-pods)
4. [Deployments — Managing Your Pods](#4-deployments)
5. [Services and Ingress — How Users Reach Your App](#5-services-and-ingress)
6. [Namespaces — Organizing Your Stuff](#6-namespaces)
7. [ConfigMaps and Secrets — Configuration](#7-configmaps-and-secrets)
8. [Jobs and CronJobs — One-Time and Scheduled Work](#8-jobs-and-cronjobs)
9. [GPU in Kubernetes — The Special Sauce](#9-gpu-in-kubernetes)
10. [Autoscaling — Saving Money Automatically](#10-autoscaling)
11. [Monitoring and Debugging](#11-monitoring-and-debugging)
12. [Performance Troubleshooting](#12-performance-troubleshooting)
13. [Security Best Practices](#13-security-best-practices)
14. [Cost Management](#14-cost-management)
15. [Common Operations Playbook](#15-common-operations-playbook)
16. [Real Scenarios From This Project](#16-real-scenarios)

---

## 1. What is AKS?

### The Simplest Explanation

Imagine you have a web app (like our weather predictor). You could run it on one computer, but:
- What if that computer crashes? App goes down.
- What if 1000 users visit at once? One computer can't handle it.
- What if you need a GPU for training but not 24/7? You're paying for idle hardware.

**Kubernetes (K8s)** solves this. It's a system that manages multiple computers (called "nodes") and automatically places your app on them, restarts it if it crashes, and scales up/down based on demand.

**AKS (Azure Kubernetes Service)** is Kubernetes running on Azure — Microsoft manages the hard parts (control plane, networking, updates). You just say "I want 2 computers with 4 CPUs each" and Azure provides them.

### Our System Right Now

```
                    ┌────────────────────────────────────┐
                    │     AKS Cluster: aks-gpu-weather   │
                    │                                    │
                    │   ┌──────────────────────────┐     │
                    │   │  CPU Node (DC2ds_v3)     │     │
                    │   │  4 vCPU, 16 GB RAM        │     │
                    │   │  Always running            │     │
                    │   │                            │     │
                    │   │  [weather-api pod]         │     │
                    │   │  [weather-ui pod]          │     │
                    │   │  [system pods x8]          │     │
                    │   └──────────────────────────┘     │
                    │                                    │
                    │   ┌──────────────────────────┐     │
                    │   │  GPU Node (NC4as_T4_v3)   │     │
                    │   │  4 vCPU, 28 GB, T4 GPU    │     │
                    │   │  SCALED TO ZERO (off)      │     │
                    │   │  Turns on only for training │     │
                    │   └──────────────────────────┘     │
                    └────────────────────────────────────┘
```

The cluster has 2 node pools, but the GPU one is currently off (saving ~$15/day). It auto-starts when we train models.

---

## 2. Nodes and Node Pools

### What's a Node?

A **node** = one virtual machine (computer) in Azure. It runs Linux, has CPU, RAM, maybe a GPU.

### What's a Node Pool?

A **node pool** = a group of identical nodes. All nodes in a pool have the same VM size, OS, and configuration.

**Why separate pools?** Because different workloads need different hardware:

| Pool | VM Type | CPU | RAM | GPU | OS disk | Purpose | Cost |
|------|---------|-----|-----|-----|---------|---------|------|
| `nodepool1` | DC4ds_v3 ×1 | 4 | 32 GB | None | 30 GB Ephemeral | Run the web app + ingress + system pods | ~$142/month |
| `gpupool1` | NC4as_T4_v3 | 4 | 28 GB | T4 (16 GB) | 30 GB Ephemeral | Train ML models | ~$0 idle, ~$0.25 per training run |

### Commands to See Your Nodes

```powershell
# List all nodes
kubectl get nodes
# Output (idle, GPU at 0):
# NAME                                STATUS   ROLES   AGE   VERSION
# aks-nodepool1-28519840-vmss000000   Ready    <none>  30m   v1.34.7

# Detailed info about a node
kubectl describe node aks-nodepool1-28519840-vmss000000

# See how much CPU/memory is used vs available
kubectl top nodes
```

### Real Example: Sizing the CPU Pool Correctly

We tried shrinking the CPU pool to a single 2 vCPU SKU (`Standard_DC2ds_v3`) to save money. The problem:

```
AKS system pods on a 2 vCPU node (kube-proxy, coredns, csi drivers,
konnectivity-agent, app-routing nginx, NVIDIA device plugin, ...)
        →  requests sum to ~1.89 / 1.9 vCPU allocatable  =  99% used

App pods we still need to schedule:
        weather-api  requests 0.25 vCPU
        crop-api     requests 0.25 vCPU
        weather-ui   requests 0.05 vCPU

Result: scheduler found no room on the CPU node, so the app pods landed on
the GPU node → GPU could never scale to 0 → huge cost regression.
```

**Fix**: use a 4 vCPU CPU node (`Standard_DC4ds_v3`). After that:

```
Allocatable on the CPU node:  ~3.86 vCPU
System pods request:           ~1.89 vCPU
App pods request:              ~0.55 vCPU
Headroom:                      ~1.42 vCPU  ✅
```

**Lessons**:
- Always check `kubectl describe node <node> | Select-String "Allocated resources"` after sizing.
- A 2 vCPU AKS node has so little allocatable space (1.9 vCPU after kube reservations) that AKS system pods alone fill it.

### How to Add/Remove Node Pools

```powershell
# Add a new node pool
az aks nodepool add \
  --resource-group rg-gpu-weather \
  --cluster-name aks-gpu-weather \
  --name mypool \
  --node-count 1 \
  --node-vm-size Standard_DC2ds_v3 \
  --mode System

# Scale a pool (change node count)
az aks nodepool scale \
  --resource-group rg-gpu-weather \
  --cluster-name aks-gpu-weather \
  --name gpupool1 \
  --node-count 0     # scale to 0 = no cost

# Delete a pool
az aks nodepool delete \
  --resource-group rg-gpu-weather \
  --cluster-name aks-gpu-weather \
  --name oldpool
```

---

## 3. Pods

### What's a Pod?

A **pod** is the smallest unit in Kubernetes. It's a wrapper around one or more containers (Docker images). Think of it as:

```
Pod = your app in a tiny sandbox, with its own IP address
```

In our system:

| Pod | What It Runs | Image |
|-----|-------------|-------|
| `weather-api-67b8b4fc99-wd89c` | Python FastAPI backend | `acrgpuweather<suffix>.azurecr.io/weather-api:v1` |
| `weather-ui-676447b695-httcg` | React frontend (nginx) | `acrgpuweather<suffix>.azurecr.io/weather-ui:v1` |

### Pod Lifecycle

```
Created → Pending → Running → (maybe) → Succeeded/Failed
              |
              └── "Pending" = waiting for a node with enough resources
```

If a pod crashes, Kubernetes automatically restarts it (up to a limit).

### Commands

```powershell
# See all pods in our namespace
kubectl get pods -n gpu-weather

# See which NODE each pod is running on
kubectl get pods -n gpu-weather -o wide

# See detailed info (events, resource usage, errors)
kubectl describe pod weather-api-xxxxx -n gpu-weather

# See recent logs from a pod
kubectl logs -n gpu-weather weather-api-xxxxx --tail=50

# Follow logs in real-time (like tail -f)
kubectl logs -n gpu-weather weather-api-xxxxx -f

# Get a shell inside a running pod (for debugging)
kubectl exec -it weather-api-xxxxx -n gpu-weather -- /bin/bash
```

### Pod States You'll See

| State | Meaning | Action |
|-------|---------|--------|
| `Running` | Everything is normal | None |
| `Pending` | No node has enough resources | Check node capacity, add nodes |
| `CrashLoopBackOff` | Pod keeps crashing and restarting | Check logs: `kubectl logs <pod>` |
| `ImagePullBackOff` | Can't download the Docker image | Check ACR connection, image name |
| `OOMKilled` | Ran out of memory | Increase memory limits in YAML |
| `Evicted` | Node ran out of disk/memory | Free resources, check node health |

---

## 4. Deployments

### What's a Deployment?

A **Deployment** tells Kubernetes: "I want X copies of this pod always running." If a pod dies, the Deployment creates a new one.

Our deployment YAML:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: weather-api          # Name of the deployment
  namespace: gpu-weather     # Which namespace
spec:
  replicas: 1                # How many copies of the pod
  selector:
    matchLabels:
      app: weather-api       # Identifies which pods belong to this deployment
  template:
    spec:
      containers:
        - name: weather-api
          image: acrgpuweather<suffix>.azurecr.io/weather-api:v1   # Docker image to run
          resources:
            requests:          # MINIMUM resources this pod needs
              cpu: "250m"      # 0.25 CPU cores
              memory: "1Gi"    # 1 GB RAM
            limits:            # MAXIMUM this pod can use
              cpu: "1500m"     # 1.5 CPU cores
              memory: "6Gi"    # 6 GB RAM
```

**requests vs limits:**
- `requests` = "I need at least this much" → used for scheduling (deciding which node)
- `limits` = "never use more than this" → if exceeded, pod gets OOMKilled

### Key Operations

```powershell
# See deployments
kubectl get deployments -n gpu-weather

# Update a deployment (after changing YAML)
kubectl apply -f k8s/backend-deployment-cpu.yaml

# Restart a deployment (pull new image)
kubectl rollout restart deployment weather-api -n gpu-weather

# Watch the rollout progress
kubectl rollout status deployment weather-api -n gpu-weather

# Rollback if something went wrong
kubectl rollout undo deployment weather-api -n gpu-weather

# Scale up to 3 replicas
kubectl scale deployment weather-ui -n gpu-weather --replicas=3
```

### Real Example: Rolling Update

When you change code and rebuild the image:

```
1. Old pod (v1) running, serving users
2. You run: kubectl rollout restart deployment weather-api
3. K8s creates NEW pod (v1 with new image)
4. Waits for new pod to pass health checks
5. Kills old pod
6. Zero downtime — users never see a blip
```

If the new pod fails health checks, the old one stays running (automatic safety net).

---

## 5. Services and Ingress

### The Problem

Pods get random IP addresses that change every time they restart. How do users find your app?

### Services — Internal DNS

A **Service** creates a stable internal hostname that routes to your pods:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: weather-api-svc
spec:
  selector:
    app: weather-api      # Routes to any pod with this label
  ports:
    - port: 8000          # The port the service listens on
      targetPort: 8000    # The port on the pod
```

Now other pods can reach the backend at `weather-api-svc:8000` — even if the actual pod dies and gets a new IP.

### Ingress — External Access

An **Ingress** exposes your services to the internet:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: weather-ingress
spec:
  rules:
    - http:
        paths:
          - path: /api       → routes to weather-api-svc:8000 (backend)
          - path: /          → routes to weather-ui-svc:80 (frontend)
```

The flow:
```
User browser → http://<ingress-ip>/api/health     (e.g. http://20.7.236.50/api/health)
                        ↓
                 Ingress Controller (nginx)
                        ↓
                 weather-api-svc (Service)
                        ↓
                 weather-api-xxxxx (Pod)
```

```powershell
# See your ingress (and its external IP)
kubectl get ingress -n gpu-weather

# See services
kubectl get svc -n gpu-weather
```

---

## 6. Namespaces

### What?

A **namespace** is like a folder for Kubernetes resources. It keeps things organized and isolated.

```powershell
# Our app lives in the "gpu-weather" namespace
kubectl get all -n gpu-weather

# System stuff lives in "kube-system"
kubectl get pods -n kube-system

# See all namespaces
kubectl get namespaces
```

Without `-n gpu-weather`, kubectl looks in the `default` namespace (which is empty for us).

**Pro tip**: Set a default namespace so you don't type `-n gpu-weather` every time:
```powershell
kubectl config set-context --current --namespace=gpu-weather
```

---

## 7. ConfigMaps and Secrets

### ConfigMap — Non-sensitive configuration

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: weather-config
data:
  CITY_NAME: "new-york"
  CITY_LAT: "40.71"
  BATCH_SIZE: "64"
  EPOCHS: "50"
```

Pods read these as environment variables. Change the config → restart the pod → new values.

### Secret — Sensitive data

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: weather-secrets
type: Opaque
stringData:
  BLOB_CONNECTION_STRING: "DefaultEndpointsProtocol=https;AccountName=..."
```

Secrets are base64-encoded (not encrypted by default — use Azure Key Vault for real security).

```powershell
# View configmap
kubectl get configmap weather-config -n gpu-weather -o yaml

# Edit configmap directly
kubectl edit configmap weather-config -n gpu-weather

# After changing configmap, restart pods to pick up changes
kubectl rollout restart deployment weather-api -n gpu-weather
```

---

## 8. Jobs and CronJobs

### Job — Run Once, Then Stop

A **Job** creates a pod, runs a task, and the pod exits. Perfect for training:

```
Job created → Pod starts → Training runs (15 min) → Pod exits → Done
```

In our system, clicking "Retrain" creates a Job:
```
train-lstm-20260402-102338     Status: Completed
train-xgboost-20260402-103504  Status: Completed
train-arima-20260402-104850    Status: Completed
```

### CronJob — Scheduled Tasks

A **CronJob** creates Jobs on a schedule:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: weather-training
spec:
  schedule: "0 2 * * 0"        # Every Sunday at 2:00 AM UTC
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: trainer
              image: acrgpuweather<suffix>.azurecr.io/weather-api:v1
              command: ["python", "-m", "scripts.train", "--model-type", "all"]
              resources:
                requests:
                  nvidia.com/gpu: 1     # Requests GPU!
```

When a Job requests `nvidia.com/gpu: 1`, the autoscaler sees "there's a pod that needs GPU but no GPU node exists" → scales up GPU node → pod runs → finishes → autoscaler removes GPU node.

```powershell
# See cronjobs
kubectl get cronjobs -n gpu-weather

# See jobs (current and recent)
kubectl get jobs -n gpu-weather

# Manually trigger a job from the cronjob template
kubectl create job manual-test --from=cronjob/weather-training -n gpu-weather

# See job pod logs
kubectl logs job/manual-test -n gpu-weather
```

---

## 9. GPU in Kubernetes

### How GPUs Work in K8s

Regular pods don't see GPUs. You need:

1. **GPU node** — a VM with a physical GPU (e.g., NVIDIA T4)
2. **NVIDIA Device Plugin** — a DaemonSet that tells Kubernetes "this node has 1 GPU"
3. **Pod requesting GPU** — `resources.limits: nvidia.com/gpu: 1`

```
Without device plugin:     With device plugin:
Node has GPU hardware       Node has GPU hardware
K8s doesn't know            K8s sees: nvidia.com/gpu: 1
Pods can't use GPU           Pods can request GPU
```

#### Important gotcha: the device plugin must tolerate the GPU taint

The stock NVIDIA device plugin DaemonSet (the one you get from
`https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.1/nvidia-device-plugin.yml`)
only tolerates `nvidia.com/gpu:NoSchedule`. If you taint your GPU pool with
`sku=gpu:NoSchedule` (which we do, to keep app pods off it), the DaemonSet
*won't run on the GPU node* — it lands on the CPU node where there is no GPU —
and the GPU node never advertises `nvidia.com/gpu` capacity. Training pods then
stay Pending forever with `Insufficient nvidia.com/gpu`.

`setup-infrastructure.ps1` patches the DaemonSet after installing it:

```powershell
$dsPatch = '{"spec":{"template":{"spec":{"tolerations":[{"effect":"NoSchedule","key":"nvidia.com/gpu","operator":"Exists"},{"effect":"NoSchedule","key":"sku","operator":"Equal","value":"gpu"}],"nodeSelector":{"kubernetes.azure.com/accelerator":"nvidia"}}}}}'
kubectl patch ds -n kube-system nvidia-device-plugin-daemonset --type=strategic -p $dsPatch
```

The `nodeSelector` also keeps the plugin off the CPU node entirely — there's
no GPU there, so it shouldn't run there.

Verify after install:

```powershell
kubectl get pods -n kube-system -l name=nvidia-device-plugin-ds -o wide
# Should show 1 pod on the GPU node (only when GPU pool has nodes)
```

### Taints and Tolerations — Keep Regular Pods Off GPU Node

GPUs are expensive (a T4 VM is ~$0.53/hr, ~$384/mo if pinned on). You don't
want your tiny frontend pod sitting on it and blocking the autoscaler from
scaling the node to 0.

**Taint** = a mark on a node that says "stay away unless you tolerate me"
**Toleration** = a pod saying "I can handle that taint"

```
GPU Node: taint = "sku=gpu:NoSchedule"
  → Regular pods: "I don't tolerate that, I'll go elsewhere"
  → Training pods: "I tolerate sku=gpu, let me in"
```

In this project, the taint is applied at node-pool creation time by
`setup-infrastructure.ps1`:

```powershell
az aks nodepool add ... --node-taints "sku=gpu:NoSchedule" --labels "sku=gpu"
```

```powershell
# Add taint to GPU node
kubectl taint nodes <gpu-node-name> sku=gpu:NoSchedule

# See node taints
kubectl describe node <gpu-node-name> | Select-String "Taint"

# Verify GPU is visible to K8s
kubectl describe node <gpu-node-name> | Select-String "nvidia"
# Should show: nvidia.com/gpu: 1
```

### Our GPU Training Flow

```
1. User clicks "Train All Models" for Jakarta
2. Backend creates K8s Job(s) via the in-cluster Kubernetes API
   (e.g. train-xgboost-20260518-052438)
3. Job pod requests nvidia.com/gpu: 1
   and tolerates sku=gpu:NoSchedule
4. No GPU node exists (scaled to 0)
5. cluster-autoscaler sees pending GPU pod
   → logs: "pod triggered scale-up: gpupool1 0->1"
6. Azure provisions the NC4as_T4_v3 VM (~3-5 min)
7. NVIDIA device plugin DaemonSet starts on the new node
   (only because we patched its tolerations + nodeSelector)
8. Node advertises nvidia.com/gpu: 1
9. Training pod is scheduled, runs (XGBoost ≈12 min on T4 for our workload),
   writes the model to blob storage, exits
10. Job is Complete; pod stays around for log retention
11. ~10 min after the last GPU pod completes, autoscaler scales gpupool1 → 0
12. GPU cost back to $0
```

End-to-end smoke test that was actually executed in this repo:
- T+0s: `POST /api/training/trigger` → Job created
- T+≈35s: autoscaler triggered scale-up
- T+≈5 min: GPU node Ready, device plugin running, pod scheduled
- T+≈18 min: Job Complete (12.6 min of training)
- T+≈28 min: gpupool1 back to 0 ✅

---

## 10. Autoscaling

### Node Autoscaling (Cluster Autoscaler)

Automatically adds/removes **nodes** based on demand.

```powershell
# Our GPU pool config
az aks nodepool show --resource-group rg-gpu-weather \
  --cluster-name aks-gpu-weather --name gpupool1 \
  --query "{min:minCount, max:maxCount, autoscale:enableAutoScaling}"
# Result: min=0, max=1, autoscale=true
```

**Scale-up** triggers when:
- A pod is `Pending` because no node has enough resources
- Example: Training job needs GPU, no GPU node exists → add one

**Scale-down** triggers when:
- A node has been underutilized for ~10 minutes
- Only DaemonSets running (system pods that run on every node)
- Example: Training done, no GPU pods → remove GPU node

### Pod Autoscaling (HPA — Horizontal Pod Autoscaler)

Automatically adds/removes **pod replicas** based on CPU/memory usage.

```powershell
# Example: auto-scale frontend between 1-5 pods based on CPU
kubectl autoscale deployment weather-ui -n gpu-weather \
  --min=1 --max=5 --cpu-percent=70

# Check HPA status
kubectl get hpa -n gpu-weather
```

We don't use HPA in this project (low traffic), but it's essential for production apps.

### How to Know if Scaling is Working

```powershell
# See cluster autoscaler activity
kubectl get events -n kube-system --sort-by='.lastTimestamp' | Select-String "scale"

# See node count over time
kubectl get nodes -w     # Watch mode — shows changes in real-time

# Check why a node was removed / not removed
kubectl describe configmap cluster-autoscaler-status -n kube-system
```

---

## 11. Monitoring and Debugging

### The Monitoring Pyramid

```
Level 1: Is it running?           kubectl get pods
Level 2: What are the error logs?  kubectl logs <pod>
Level 3: Why did it crash?         kubectl describe pod <pod>
Level 4: Is it slow?               kubectl top pods
Level 5: Deep diagnostics          Azure Monitor / Log Analytics
```

### Essential Commands

```powershell
# ===== HEALTH CHECK =====
# Quick status of everything
kubectl get all -n gpu-weather

# ===== LOGS =====
# See last 50 lines of backend logs
kubectl logs -n gpu-weather -l app=weather-api --tail=50

# Follow logs in real-time
kubectl logs -n gpu-weather -l app=weather-api -f

# See logs from a crashed pod (previous instance)
kubectl logs -n gpu-weather <pod-name> --previous

# ===== RESOURCE USAGE =====
# CPU and memory usage per pod
kubectl top pods -n gpu-weather

# CPU and memory usage per node
kubectl top nodes

# ===== EVENTS =====
# Recent events (scheduling, errors, scaling)
kubectl get events -n gpu-weather --sort-by='.lastTimestamp' | Select-Object -Last 20

# ===== NETWORK =====
# Test if pod can reach external API
kubectl exec -n gpu-weather <pod> -- curl -s https://api.open-meteo.com/v1/forecast?latitude=0&longitude=0
```

### Setting Up Azure Monitor (Portal)

1. Go to Azure Portal → your AKS cluster
2. Click **Insights** in the left menu
3. Enable Container Insights if prompted
4. You'll see:
   - Node CPU/memory charts
   - Pod restart counts
   - Failed pod deployments
   - Container logs searchable via KQL

### Useful KQL Queries (in Azure Portal > Log Analytics)

```kusto
// Find all errors in backend pod
ContainerLogV2
| where ContainerName == "weather-api"
| where LogMessage contains "ERROR"
| order by TimeGenerated desc
| take 20

// Find OOM killed pods
KubeEvents
| where Reason == "OOMKilling"
| order by TimeGenerated desc

// See pod restart history
KubePodInventory
| where Namespace == "gpu-weather"
| where RestartCount > 0
| project TimeGenerated, Name, RestartCount
```

---

## 12. Performance Troubleshooting

### Problem: Pod is Pending

```
Symptom: kubectl get pods shows "Pending" for a long time
```

**Diagnosis:**
```powershell
kubectl describe pod <pending-pod> -n gpu-weather
# Look at the Events section at the bottom
```

**Common causes:**

| Event Message | Meaning | Fix |
|---------------|---------|-----|
| `Insufficient cpu` | Node doesn't have enough CPU | Add bigger node or reduce requests |
| `Insufficient memory` | Node doesn't have enough RAM | Add bigger node or reduce requests |
| `Insufficient nvidia.com/gpu` | No GPU node available | Wait for autoscaler (~5 min) or manually scale |
| `node(s) had untolerated taint` | Pod can't run on tainted node | Add toleration to pod or remove taint from node |
| `no nodes available to schedule` | All nodes are full | Add more nodes |

### Problem: Pod Keeps Crashing (CrashLoopBackOff)

```powershell
# Step 1: See why it crashed
kubectl logs <pod-name> -n gpu-weather --previous

# Step 2: Check if OOM
kubectl describe pod <pod-name> -n gpu-weather | Select-String "OOM|Reason|Last State"

# Step 3: Common fixes
# - OOMKilled: increase memory limits in deployment YAML
# - Python error: fix the code, rebuild image
# - Config error: check env vars in configmap
```

### Problem: App is Slow

```powershell
# Check pod resource usage
kubectl top pods -n gpu-weather
# If CPU is near the limit → increase CPU limits or add replicas

# Check node resource usage  
kubectl top nodes
# If node is maxed → upgrade node pool or add nodes

# Check if too many pods on one node
kubectl get pods -o wide -n gpu-weather
# Spread pods across nodes using anti-affinity rules
```

### Problem: API Returns 502/503

502/503 from nginx usually means the backend pod is down or overloaded.

```powershell
# 1. Is the pod running?
kubectl get pods -n gpu-weather -l app=weather-api

# 2. Check for restarts
kubectl describe pod -l app=weather-api -n gpu-weather | Select-String "Restart"

# 3. Check logs
kubectl logs -l app=weather-api -n gpu-weather --tail=30

# 4. Check ingress
kubectl get ingress -n gpu-weather
kubectl describe ingress weather-ingress -n gpu-weather
```

---

## 13. Security Best Practices

### Network Security

```
                    Internet
                       ↓
                 [Azure Load Balancer]    ← Only ports 80/443 open
                       ↓
                 [Ingress Controller]     ← Routes traffic to services
                       ↓
              ┌────────┴────────┐
         [frontend]       [backend]       ← Internal only, no public IPs
                              ↓
                     [Azure Blob Storage]  ← Accessed via Managed Identity
```

**Things we do right:**
- Pods don't have public IPs — only the Ingress has one
- Backend uses Managed Identity (no passwords stored) to access Blob Storage
- Secrets in Kubernetes (not hardcoded in code)

**Things to add for production:**
- Enable HTTPS with TLS certificate
- Restrict CORS to your domain (currently `allow_origins=["*"]`)
- Enable network policies (restrict pod-to-pod communication)
- Use Azure Key Vault instead of K8s Secrets
- Enable pod security standards

### RBAC — Who Can Do What

```yaml
# Our training RBAC: lets the backend pod create training jobs
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: training-job-manager
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list"]
```

This follows **least privilege** — the backend can only manage jobs and read pod logs, nothing else.

```powershell
# See roles in our namespace
kubectl get roles -n gpu-weather

# See who has what access
kubectl get rolebindings -n gpu-weather
```

### Managed Identity (No Passwords!)

Instead of storing a connection string for Blob Storage, we use Azure Managed Identity:

```
Old way (insecure):
  Pod → uses connection string (password) → Blob Storage
  Problem: password in environment variable, can be leaked

Our way (secure):
  Pod → uses Managed Identity (automatic token) → Blob Storage
  No password stored anywhere. Azure handles authentication.
```

---

## 14. Cost Management

### Current Cost Breakdown

| Resource | SKU | Monthly Cost | Can Save? |
|----------|-----|--------------|-----------|
| CPU Node Pool | DC4ds_v3 ×1 (4 vCPU) | ~$142 | Always on when cluster is running |
| GPU Node Pool | NC4as_T4_v3 (T4) | $0 (idle) | Autoscales to 0; only billed during scale-up + training + cooldown |
| Ephemeral OS disks | 30 GB on both pools | $0 | Uses VM local SSD (no managed disk cost) |
| Load Balancer | Standard | ~$18 | Always on with the cluster |
| Container Registry | Basic | ~$5 | Always on |
| Blob Storage | Standard LRS | ~$2 | Always on |
| Key Vault + App Insights | | ~$5 | Always on |
| **Cluster stopped (`az aks stop`)** | | **~$5–10** | All compute deallocated |
| **Cluster running, no training** | | **~$170** | |
| **Cluster running + a few training runs** | | **~$170 + ~$0.25 each** | |

### Cost-Saving Commands

```powershell
# Check GPU node status (is it costing money right now?)
kubectl get nodes -l agentpool=gpupool1
# No results = scaled to 0 = $0/hr
# 1 node showing  = ~$0.53/hr while it lives

# Stop the ENTIRE cluster (drops compute to $0, app goes offline)
az aks stop --resource-group rg-gpu-weather --name aks-gpu-weather

# Restart cluster later
az aks start --resource-group rg-gpu-weather --name aks-gpu-weather

# Nuclear option: delete everything, $0 forever
az group delete --name rg-gpu-weather --yes --no-wait
# or use the helper:  .\teardown.ps1 -Mode full
```

### Why GPU Auto-Scale Works

The key insight: training takes ~13 minutes for XGBoost on our workload.
If you train once per week:

```
GPU billed window per run (scale-up + train + cooldown):  ≈28 min ≈ 0.47 hr
GPU on cost:   0.47 hr × ~$0.53/hr  = ~$0.25 per training run
GPU off cost:  rest of the week     = $0

Monthly (weekly retrain):  ~4 runs × $0.25 = ~$1.00/month for GPU
vs. keeping GPU pinned at 1 node 24/7:   ~$384/month
```

That's a **>99% reduction** from autoscale-to-0 plus a matching taint.

---

## 15. Common Operations Playbook

### Deploy New Code

```powershell
# 1. Build image(s) — sends code to Azure, builds in cloud
cd c:\labs\tech\aksgpu-temp
$cfg = Get-Content .\.infra-config.json | ConvertFrom-Json
az acr build --registry $cfg.AcrName --image weather-api:v1 .\backend  --no-logs -o none   # ~7 min
az acr build --registry $cfg.AcrName --image weather-ui:v1  .\frontend --no-logs -o none   # ~2 min
az acr build --registry $cfg.AcrName --image crop-api:v1    .\crop     --no-logs -o none   # ~7 min

# 2. Restart pods to pull new image
kubectl rollout restart deployment weather-api weather-ui crop-api -n gpu-weather

# 3. Verify
Start-Sleep 90
kubectl get pods -n gpu-weather
```

### Change Environment Variables

```powershell
# Edit configmap
kubectl edit configmap weather-config -n gpu-weather
# (opens in editor — change values, save, quit)

# Restart to pick up changes
kubectl rollout restart deployment weather-api -n gpu-weather
```

### Train Models for a New City

Just use the UI:
1. Select city in dropdown
2. Go to Training tab
3. Click "Download Data"
4. Click "Train All Models"
5. Watch live logs in Recent Training Jobs

### Manually Scale GPU

```powershell
# Force GPU on (for testing/demo)
az aks nodepool scale --resource-group rg-gpu-weather \
  --cluster-name aks-gpu-weather --name gpupool1 --node-count 1

# Force GPU off (save money immediately, don't wait for autoscaler)
az aks nodepool scale --resource-group rg-gpu-weather \
  --cluster-name aks-gpu-weather --name gpupool1 --node-count 0
```

### Emergency: App is Down

```powershell
# 1. Check pods
kubectl get pods -n gpu-weather
# If no pods or all crashing → 

# 2. Check events
kubectl get events -n gpu-weather --sort-by='.lastTimestamp' | Select-Object -Last 10

# 3. Check node health
kubectl get nodes
kubectl describe node <node-name> | Select-String "Condition|Ready|Pressure"

# 4. Nuclear restart
kubectl delete pods --all -n gpu-weather
# Deployments will auto-create new pods
```

---

## 16. Real Scenarios From This Project

### Scenario 1: "Pods landed on the GPU node"

**What happened**: Backend and frontend ran on the $15/day GPU node instead of the CPU node. GPU couldn't scale down.

**Root cause**: GPU node had no taint, and it had more resources (28GB vs 16GB), so Kubernetes preferred it.

**Fix**:
```powershell
# Add taint so regular pods can't go there
kubectl taint nodes <gpu-node> sku=gpu:NoSchedule
# Restart pods so they move to CPU node
kubectl rollout restart deployment weather-api weather-ui -n gpu-weather
```

**Lesson**: Always taint special-purpose nodes (GPU, high-memory, etc.)

### Scenario 2: "OOMKilled — pod runs out of memory"

**What happened**: Validation loaded ML models (PyTorch ~1.5GB) + data processing. Total exceeded 2GB limit.

**Root cause**: Memory limit too low in deployment YAML.

**Fix**: Increased from `memory: 2Gi` to `memory: 6Gi` in `k8s/backend-deployment-cpu.yaml`.

**How to detect**:
```powershell
kubectl describe pod <pod> -n gpu-weather | Select-String "OOM|Reason"
# Output: Reason: OOMKilled
```

**Lesson**: Set memory limits based on actual peak usage, not average. PyTorch is memory-hungry.

### Scenario 3: "Wrong model loaded for the wrong city"

**What happened**: User selected Jakarta, but validation used the New York model → terrible accuracy.

**Root cause**: `predictor.load_model()` didn't filter by city — it loaded whichever model name sorted first alphabetically.

**Fix**: Added `city` parameter to model loading. Every predict/validate request now passes the selected city.

**Lesson**: When your app has per-tenant data (models per city), make sure your loading logic filters correctly.

### Scenario 4: "Training job stuck in Pending"

**What happened**: Training job showed "Pending" for 5+ minutes.

**Root cause**: GPU node pool was scaling from 0 to 1. The NVIDIA T4 VM takes ~3-5 minutes to provision in Azure.

**How to check**:
```powershell
kubectl describe pod <pending-pod> -n gpu-weather
# Events: "pod triggered scale-up" → autoscaler is working, just wait
```

**Lesson**: GPU scale-up is not instant. If you need faster training starts, keep min-count at 1 (but that costs $15/day).

### Scenario 5: "Build succeeded but old code still running"

**What happened**: `az acr build` succeeded, but the app still showed old behavior.

**Root cause**: Forgot to restart the deployment after building. The pod was still running the old cached image.

**Fix**:
```powershell
kubectl rollout restart deployment weather-api -n gpu-weather
```

**Lesson**: Building the image and deploying it are TWO separate steps. Build = push to registry. Deploy = restart pod to pull new image.

---

## Glossary

| Term | What It Means |
|------|---------------|
| **AKS** | Azure Kubernetes Service — managed K8s on Azure |
| **Cluster** | The whole Kubernetes environment (nodes + control plane) |
| **Node** | One virtual machine (computer) in the cluster |
| **Node Pool** | A group of identical nodes |
| **Pod** | The smallest deployable unit — wraps a container |
| **Deployment** | Manages pod replicas, rolling updates, rollbacks |
| **Service** | Stable internal DNS name for a set of pods |
| **Ingress** | Routes external traffic to internal services |
| **Namespace** | A virtual sub-cluster for organizing resources |
| **ConfigMap** | Key-value config that pods read as env vars |
| **Secret** | Like ConfigMap but for sensitive data |
| **Job** | Run-once task (creates a pod, runs, exits) |
| **CronJob** | Scheduled recurring Job |
| **DaemonSet** | Pod that runs on every node (e.g., monitoring agent) |
| **Taint** | Mark on a node: "don't schedule pods here unless they tolerate me" |
| **Toleration** | Pod says: "I can handle this taint, let me in" |
| **RBAC** | Role-Based Access Control — who can do what |
| **HPA** | Horizontal Pod Autoscaler — auto-scale pod count |
| **Cluster Autoscaler** | Auto-scale node count |
| **ACR** | Azure Container Registry — where Docker images are stored |
| **OOMKilled** | Out Of Memory Killed — pod exceeded memory limit |
| **ImagePullBackOff** | Can't download the Docker image from registry |
| **CrashLoopBackOff** | Pod keeps crashing, K8s keeps restarting it |
| **kubectl** | Command-line tool to interact with Kubernetes |
