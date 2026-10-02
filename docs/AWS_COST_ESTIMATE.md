# AWS Cost Estimate — before anything is provisioned (Phase 12.2.8)

> **The decision this page is for:** whether, and in which shape, ZenFlow moves to AWS. Nothing has
> been provisioned. The Terraform (`docs/INFRA.md`) is ready and is applied only after the owner
> picks an option here.

**Prices:** on-demand, region **il-central-1 (Tel Aviv)**, read from the public AWS Price List on
2026-10-02. Monthly means 730 hours. VAT is not included. The arithmetic is reproducible: every unit
price is in the table at the end. The first 100 GB per month of data out to the internet is free,
and a clinic uses a few GB.

## The answer in one table

| Option | What it is | Per month |
|---|---|---|
| **A. Managed, CPU Ollama** (recommended) | The Terraform as written: ECS Fargate, RDS, ElastiCache, ALB + WAF, Ollama on `c7i.xlarge` (4 vCPU) | **≈ $325** |
| A+. Managed, faster CPU Ollama | Same, Ollama on `c7i.2xlarge` (8 vCPU) | ≈ $472 |
| A-GPU. Managed, GPU Ollama | Same, Ollama on `g5.xlarge` (NVIDIA A10G) | ≈ $1,039 |
| **B. Smallest viable: one server** | One EC2 instance running the existing `docker-compose.yml` (web, bots, worker, Postgres, Redis, Ollama), daily disk snapshots | **≈ $156–$189** |

**Ollama is the largest line in every option.** The owner decided to keep it (Q3). A GPU in Tel Aviv
means `g5.xlarge` at ~$861/month on its own: the cheaper `g4dn` GPUs are **not offered** in this region.

## Option A — the managed architecture (the Terraform's defaults)

| Item | Per month |
|---|---|
| ECS Fargate — web (0.5 vCPU, 1 GB) | $23.07 |
| ECS Fargate — bots (0.5 vCPU, 1 GB) | $23.07 |
| ECS Fargate — worker (0.25 vCPU, 0.5 GB) | $11.53 |
| RDS Postgres `db.t4g.micro`, single-AZ | $13.14 |
| RDS storage 20 GB gp3 (14-day PITR backups fit in the free allowance at this size) | $3.06 |
| ElastiCache Redis `cache.t4g.micro` | $13.07 |
| Application Load Balancer (+ ~0.5 capacity unit) | $22.38 |
| Public IPv4 addresses: ALB ×2 + 3 tasks ($0.005/h each) | $18.25 |
| WAF: web ACL + 5 rules + ~1M requests | $10.60 |
| Secrets Manager ×3, KMS key, CloudWatch (~3 GB logs, 8 alarms) | $4.59 |
| Route 53 zone, ECR images, S3 media, SNS, budget | $1.10 |
| **Subtotal: the app without Ollama** | **$143.85** |
| Internal NLB for Ollama's TLS (+ a little capacity) | $20.24 |
| Ollama instance's public IPv4 + 100 GB disk for models | $14.21 |
| **Ollama's surroundings** | **$34.45** |
| Ollama `c7i.xlarge` (4 vCPU, 8 GB, CPU) | $146.80 |
| **Total, Option A** | **≈ $325** |

Swap the Ollama instance: `c7i.2xlarge` (8 vCPU, CPU) $293.61 → **≈ $472**; `g5.xlarge` (GPU)
$860.78 → **≈ $1,039**.

### What changes the bill in Option A

| Change | Per month |
|---|---|
| Private subnets behind a NAT gateway (instead of tasks with public IPs) | **+ $30.00** |
| Multi-AZ database (a standby in a second zone; survives a zone outage) | **+ $16.91** |
| Graviton (ARM) images for the three Fargate services | − $11.53 |
| Valkey instead of Redis (same protocol) | − $2.61 |
| No WAF (removes a protection layer; not recommended for health data) | − $10.60 |
| A 1-year Savings Plan / reserved database | roughly − 25% to − 40% on compute and the database (approximate; varies by commitment) |
| A **staging** copy left running | about **+ $11 per day**: create it for a test, then `terraform destroy` |

## Option B — smallest viable: one server with the existing compose stack

The same images and `docker-compose.yml` (12.2.1) on one EC2 instance, with TLS from Caddy and
Let's Encrypt on the box. Postgres and Redis run as containers, with daily EBS snapshots for backups.

| Instance (16 GB RAM: Ollama + Postgres + the app fit) | Instance | + 100 GB disk, snapshots, IPv4, DNS | Per month |
|---|---|---|---|
| `m7g.xlarge` — 4 vCPU Graviton (needs ARM images) | $139.43 | $16.41 | **≈ $156** |
| `m7i.xlarge` — 4 vCPU | $172.24 | $16.41 | **≈ $189** |
| `c7i.2xlarge` — 8 vCPU (faster Ollama) | $293.61 | $16.41 | ≈ $310 |

**What Option B gives up**, against roughly $140–170 a month saved:

| | A: managed | B: one server |
|---|---|---|
| Database backups | RDS: automatic, point-in-time to the second for 14 days, encrypted | Disk snapshots once a day (lose up to a day), our own restore drill |
| Security patches | RDS/ElastiCache/Fargate patched by AWS | We patch the OS, Postgres and Redis |
| A crash or a bad deploy | ECS restarts tasks, the circuit breaker rolls back | The whole clinic is down until the box is back |
| Encryption, private network, WAF | built in | encrypted disk only; everything else ourselves |
| Moving up later | change a variable | migrate again |

## Recommendation

**Option A with the CPU Ollama (`c7i.xlarge`), ≈ $325/month. Set the budget alarm at $400.**
- Health data argues for the managed parts: point-in-time database recovery, automatic patching,
  encryption, and a private network are what Option B would make us build and run ourselves.
- **Before choosing the GPU, measure the CPU on staging.** `gemma3` (4B) on 4 vCPU should answer an
  intake question in roughly 10–20 seconds. The diagnosis and points run as background jobs, so a
  minute or two there is tolerable. These speeds are an expectation to measure, not a measurement.
  If the CPU is too slow for patients, `c7i.2xlarge` (+ $147) is the next step. The GPU (+ $714) is
  hard to justify for one clinic.
- **Later savings once it is stable:** Graviton images (− $12) and a 1-year Savings Plan (roughly
  − 25–40%).
- **If the budget matters more than the managed parts:** Option B on `m7i.xlarge` (≈ $189) is the
  honest smallest choice, with the trade-offs listed above.

**What the owner decides here:** A or B; the Ollama size; Multi-AZ (+ $17) yes or no; and the
budget alarm amount. Then, in order: `docs/INFRA.md`, then the migration runbook (12.2.9).

## Unit prices used (il-central-1, on-demand, 2026-10-02)

| Price | USD |
|---|---|
| Fargate x86: vCPU-hour / GB-hour | 0.0518144 / 0.0056896 |
| Fargate ARM: vCPU-hour / GB-hour | 0.0414515 / 0.0045517 |
| RDS PostgreSQL `db.t4g.micro` single-AZ / multi-AZ (hour) | 0.018 / 0.037 |
| RDS gp3 storage single-AZ / multi-AZ (GB-month) | 0.153 / 0.305 |
| RDS backup storage beyond the free allowance (GB-month) | 0.105 |
| ElastiCache `cache.t4g.micro` Redis / Valkey (hour) | 0.0179 / 0.01432 |
| ALB hour / LCU-hour | 0.02646 / 0.0084 |
| NLB hour / NLCU-hour | 0.02646 / 0.0063 |
| Public IPv4 address (hour) | 0.005 |
| NAT gateway hour / GB processed | 0.0504 / 0.0504 |
| WAF web ACL / rule (month), per million requests | 5.00 / 1.00, 0.60 |
| Secrets Manager secret (month) | 0.40 |
| CloudWatch logs ingest (GB) / storage (GB-month) / alarm (month) | 0.50 / 0.03 / 0.10 |
| EBS gp3 (GB-month) / snapshot (GB-month) | 0.1056 / 0.06 |
| EC2 `c7i.xlarge` / `c7i.2xlarge` / `g5.xlarge` (hour) | 0.2011 / 0.4022 / 1.17915 |
| EC2 `m7i.xlarge` / `m7g.xlarge` (hour) | 0.23594 / 0.191 |
| `g4dn` (any size) | not offered in il-central-1 |
| KMS key (month), Route 53 zone (month), ECR (GB-month) | 1.00, 0.50, 0.10 |

Sources: `pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<service>/current/il-central-1/index.json`
(AmazonECS, AmazonRDS, AmazonElastiCache, AWSELB, AmazonVPC, awswaf, AWSSecretsManager,
AmazonCloudWatch, AmazonEC2) and the EC2 on-demand price map behind aws.amazon.com/ec2/pricing.
Prices change; re-read them the week the decision is made.
