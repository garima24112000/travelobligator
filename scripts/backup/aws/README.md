# GitHub-independent backup automation (AWS) — prepared, not provisioned

GitHub Actions has refused to run any job for this account since 2026-11-06, including the daily backup
(`neon-backup.yml`) and the hourly monitor (`production-monitor.yml`). This directory holds a replacement in the
existing AWS account. **Nothing here has been deployed.** The owner approved the design on 2026-11-10 and has to
approve before any resource is created or any schedule is switched on, because some parts may cost money (see
"Cost").

| File | What it is |
| --- | --- |
| `lambda_handler.py` | One handler, three modes, each fixed by its function's configuration: `backup`, `verify` and `monitor` |
| `Dockerfile` | The arm64 Lambda image: PostgreSQL 16 client, GnuPG 2.4, AWS CLI, the **unchanged** `../neon_s3_backup.py`, and hash-pinned Python packages |
| `requirements.txt` | The runtime client and boto3, pinned by version and SHA-256 |
| `template.json` | CloudFormation: three functions, three roles, three schedules, five alarms, one SNS email topic |

Tests: `backend/app/tests/scripts/test_aws_backup_automation.py` (hermetic: real GnuPG, stand-ins for AWS).

## What it does once activated

| Function | When | What | Permissions |
| --- | --- | --- | --- |
| `travelobligator-neon-backup` | daily 03:37 UTC | Runs `neon_s3_backup.py`, the same script and safety contract as the GitHub workflow: dump, check, encrypt, prove decryption, upload without overwriting, compare the checksum. The two secrets are read from Parameter Store at invocation. | read the two parameters; put and get objects under `neon/` |
| `travelobligator-backup-verify` | Sundays 05:07 UTC | Downloads the newest `neon/` object, decrypts and authenticates it, and reads its table of contents (`pg_restore --list`). No database. | read the passphrase parameter; list `neon/`; get objects under `neon/` |
| `travelobligator-backup-monitor` | hourly at :17 | Checks the newest backup is at most 36 hours old, and that the **public frontend page** answers 200. It never contacts the backend, by the owner's decision (no keepalive). | list `neon/` |

Any failure fails the invocation. Five CloudWatch alarms email the owner through SNS:
- backup failed;
- backup did not run in a day (an absent run is an alert);
- verification failed;
- monitor problem (with an "OK" email when it clears);
- monitor did not run for three hours.

None of these functions can delete an object. The 7-day lifecycle rule stays the bucket's only expiry. The monthly
full restore drill (`../restore_drill.py`, docs/30) stays a manual step.

Object names are `neon/travelobligator-<UTC>-runlocal-1.pgdump.gpg`. The script's run number is GitHub's, so a run
outside GitHub writes `runlocal`. The timestamp keeps names unique, and an existing object is never overwritten.

## Cost (approval needed)

| Item | Expected use | Charge |
| --- | --- | --- |
| Lambda | ~800 invocations and ~5,000 GB-seconds a month | within the always-free allowance (1M requests, 400,000 GB-s) |
| EventBridge Scheduler | ~760 invocations a month | within the free allowance (14M a month) |
| SNS email | a few emails | within the free allowance (1,000 a month) |
| CloudWatch | 5 alarms; logs kept 30 days, well under 1 GB | within the free allowance (10 alarms, 5 GB logs) |
| Parameter Store | 2 standard SecureString parameters, default `aws/ssm` key | no charge |
| **ECR** | one image, about 0.3–0.5 GB (two kept at most) | **about $0.03–0.05 a month** once any free ECR allowance has run out |
| **S3 requests** | ~760 LIST/GET a month | **under $0.01 a month** after the 12-month S3 free tier |

**Expected total: under $0.10 a month, and not zero.** That's why it needs the owner's approval. An AWS Budgets alert
(two are free) on the account is recommended before activation.

## Steps, each one only with the owner's approval

1. **Check the bucket, read-only.** Its policy must not exclude the new roles. Its default encryption must be
   SSE-S3: an SSE-KMS key of the owner's would need `kms:GenerateDataKey` / `kms:Decrypt` for the roles, which the
   template doesn't grant. Versioning and the lifecycle rule are as docs/30 describes.
2. **Create the two secrets yourself** in the console: Systems Manager → Parameter Store → Create, type
   **SecureString**, key `alias/aws/ssm`. Names `/travelobligator/backup/neon-database-url` (the backup role's
   DIRECT URL) and `/travelobligator/backup/encryption-passphrase` (the SAME passphrase as the existing backups).
   Never put them on a command line, in the template or in a file in the repository.
3. **Create the image repository** (ECR, private, scan on push, with a lifecycle rule keeping two images), then
   build and push:
   ```bash
   docker build --platform linux/arm64 -f scripts/backup/aws/Dockerfile -t travelobligator-backup:<commit> .
   # docker tag / docker push to <account>.dkr.ecr.us-east-1.amazonaws.com/travelobligator-backup:<commit>
   ```
   Note the pushed image's digest (`@sha256:…`). The template accepts only a digest, never a tag.
4. **Deploy inactive:**
   ```bash
   aws cloudformation deploy --stack-name travelobligator-backup --template-file scripts/backup/aws/template.json \
       --capabilities CAPABILITY_IAM --region us-east-1 \
       --parameter-overrides ImageUri=<…@sha256:…> AlertEmail=<owner address> Activate=false
   ```
   Confirm the SNS subscription from the email AWS sends.
5. **Test by hand, while still inactive:**
   - `aws lambda invoke --function-name travelobligator-backup-monitor /dev/stdout`;
   - then the backup function;
   - then the verify function.

   Read each log. Then run `../restore_drill.py` on the new object. A backup counts only after the drill passes.
6. **Activate** (owner approval): deploy again with `Activate=true`. When GitHub Actions returns, leave
   `NEON_BACKUP_SCHEDULE_ENABLED` off, so that only one schedule writes backups.
7. **Remove** if wanted: delete the stack, the ECR repository and the two parameters. Existing backups expire
   under the lifecycle rule.
