# patchmon-awx — Deployment Guide

---

## Project structure

```
patchmon-awx/
├── ansible.cfg
├── requirements.txt                         # Python: requests>=2.25.1
├── .gitignore
│
├── collections/
│   ├── requirements.yml                     # ansible.posix, community.general, ansible.windows
│   └── ansible_collections/patchmon/
│       └── dynamic_inventory/               # plugin bundled — no Galaxy needed
│           ├── galaxy.yml
│           ├── meta/runtime.yml
│           └── plugins/inventory/
│               └── dynamic_inventory.py
│
├── inventory/
│   └── patchmon_inventory.yml               # plugin: line only — NO Jinja2 lookups
│
├── group_vars/
│   ├── linux.yml                            # ansible_user: exadmn + become
│   ├── professional.yml
│   ├── advanced.yml
│   ├── basic.yml
│   ├── cpanel.yml
│   └── windows.yml                          # winrm connection settings
│
└── playbooks/
    ├── test_connectivity.yml                # run this first
    ├── linux_patch.yml                      # apt / dnf / yum auto-detect
    ├── cpanel_upcp.yml                      # /scripts/upcp --force
    └── windows_updates.yml                  # win_updates module
```

---

## Step 1 — Verify the PatchMon API URL and credentials

Before touching AWX, confirm the API works with curl:

```bash
curl -s \
  -u "YOUR_API_KEY:YOUR_API_SECRET" \
  -H "Accept: application/json" \
  "https://your-patchmon-server/api/v1/api/hosts/" \
  | python3 -m json.tool | head -40
```

You must see JSON like `{"hosts": [...]}` — not HTML, not a redirect.
If you see HTML, the URL is wrong. The path must end with `/api/v1/api/hosts/`.

---

## Step 2 — Push repo to Git

```bash
cd patchmon-awx
git init
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:yourorg/patchmon-awx.git
git push -u origin main
```

If the repo is private, you will need a deploy key (Step 4).

---

## Step 3 — Create AWX Machine Credential (Linux SSH)

```
AWX → Credentials → Add
────────────────────────────────────────────
Name:                    Linux - exadmn SSH
Credential Type:         Machine
Username:                exadmn
SSH Private Key:         [paste private key]
Privilege Escalation:    sudo
Privilege Esc. Username: root
```

---

## Step 4 — Create AWX SCM Credential (if private Git repo)

```
AWX → Credentials → Add
────────────────────────────────────────────
Name:            Git Deploy Key
Credential Type: Source Control
SSH Private Key: [paste deploy key]
```

---

## Step 5 — Create AWX Project

```
AWX → Projects → Add
────────────────────────────────────────────
Name:                  PatchMon AWX
Source Control Type:   Git
Source Control URL:    git@github.com:yourorg/patchmon-awx.git
Branch:                main
Credential:            Git Deploy Key   ← only if private repo
Options:
  ✓ Clean
  ✓ Update Revision on Launch
```

Click Save → Sync. Wait for green status before continuing.

---

## Step 6 — Create AWX Inventory

```
AWX → Inventories → Add → Inventory
────────────────────────────────────────────
Name: PatchMon Inventory
```

Then open it → **Sources → Add Source**:

```
Name:           PatchMon API
Source:         Sourced from a Project
Project:        PatchMon AWX
Inventory file: inventory/patchmon_inventory.yml

Environment Variables:
  PATCHMON_API_URL:    https://your-patchmon-server/api/v1/api/hosts/
  PATCHMON_API_KEY:    your_api_key
  PATCHMON_API_SECRET: your_api_secret

Update options:
  ✓ Update on Launch
  ✓ Overwrite
  ✓ Overwrite vars
```

Click **Save** → **Sync**.
Open the **Hosts** tab — your servers should appear grouped by PatchMon host_group.

---

## Step 7 — Tag your servers in PatchMon

Each server needs host_groups in PatchMon that match the group_vars files.
A server can have multiple tags.

| PatchMon host_group | Who gets it |
|---|---|
| `professional` | professional tier servers |
| `advanced` | advanced tier servers |
| `basic` | basic tier servers |
| `cpanel` | any server with cPanel installed |
| `linux` | all Linux servers |
| `windows` | all Windows servers |

Example: a cPanel server on the professional tier should have:
`professional` + `cpanel` + `linux`

---

## Step 8 — Create Job Template: Test Connectivity

```
AWX → Templates → Add → Job Template
────────────────────────────────────────────
Name:        Test - Linux Connectivity
Playbook:    playbooks/test_connectivity.yml
Inventory:   PatchMon Inventory
Credential:  Linux - exadmn SSH
Limit:       professional,advanced,basic
✓ Enable Privilege Escalation
```

**Run this first.** If any host fails, fix SSH access before running patch jobs.

---

## Step 9 — Create Job Template: Linux Package Update

```
AWX → Templates → Add → Job Template
────────────────────────────────────────────
Name:        Linux - Package Update
Playbook:    playbooks/linux_patch.yml
Inventory:   PatchMon Inventory
Credential:  Linux - exadmn SSH
Limit:       professional,advanced,basic
✓ Enable Privilege Escalation
```

Add Survey (Templates → [template] → Survey → Add):

| Question | Variable | Type | Default |
|---|---|---|---|
| Batch size (hosts at once) | batch_size | Integer | 10 |
| Allow reboot if required? | reboot_allowed | Multiple choice | false |

---

## Step 10 — Create Job Template: cPanel upcp

```
AWX → Templates → Add → Job Template
────────────────────────────────────────────
Name:        cPanel - Force upcp
Playbook:    playbooks/cpanel_upcp.yml
Inventory:   PatchMon Inventory
Credential:  Linux - exadmn SSH
Limit:       cpanel
✓ Enable Privilege Escalation
```

Add Survey:

| Question | Variable | Type | Default |
|---|---|---|---|
| Batch size | batch_size | Integer | 3 |
| Run upcp --force? | run_upcp_force | Multiple choice | true |

---

## Step 11 — Create Job Template: Windows Updates (optional)

```
AWX → Templates → Add → Job Template
────────────────────────────────────────────
Name:        Windows - Security Updates
Playbook:    playbooks/windows_updates.yml
Inventory:   PatchMon Inventory
Credential:  Windows Admin Credential
Limit:       windows
```

Windows Machine Credential:
```
AWX → Credentials → Add
  Credential Type:  Machine
  Username:         Administrator (or domain\user)
  Password:         [windows admin password]
```

---

## Step 12 — Create AWX Workflow (optional but recommended)

Chain all three jobs so they run in sequence automatically:

```
AWX → Templates → Add → Workflow Job Template
────────────────────────────────────────────
Name: Full Patch Run

Visualizer:
  [Linux - Package Update]
          ↓ (on success)
  [cPanel - Force upcp]
          ↓ (on success)
  [Test - Linux Connectivity]
```

---

## Step 13 — Schedule automated runs

```
Linux - Package Update → Schedules → Add
────────────────────────────────────────────
Name:      Weekly patch — all Linux
Start:     Sunday 01:00 UTC
Frequency: Weekly
Survey defaults: batch_size=10, reboot_allowed=false
```

```
cPanel - Force upcp → Schedules → Add
────────────────────────────────────────────
Name:      Weekly upcp — cPanel servers
Start:     Sunday 02:00 UTC   ← 1 hour after linux_patch
Frequency: Weekly
```

---

## Troubleshooting

**Inventory returns 0 hosts:**
Check the Environment Variables on the inventory source, not the job template.
They must be on the SOURCE, not just the template.

**HTML error / MissingSchema:**
`PATCHMON_API_URL` is wrong. It must be:
`https://your-patchmon-server/api/v1/api/hosts/`
Verify with curl first (see Step 1).

**SSH permission denied:**
The `exadmn` user must exist on the target and your AWX private key
must be in `~exadmn/.ssh/authorized_keys`.

**upcp not running:**
The host is in the `cpanel` group but the playbook still checks that
`/scripts/upcp` exists and the `cpanel` systemd service is active.
If either check fails, it skips gracefully.
