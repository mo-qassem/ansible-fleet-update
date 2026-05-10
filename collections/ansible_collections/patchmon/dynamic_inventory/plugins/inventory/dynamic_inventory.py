# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
    name: dynamic_inventory
    plugin_type: inventory
    short_description: PatchMon dynamic inventory plugin
    description:
        - Queries the PatchMon REST API and builds an Ansible inventory.
        - Automatically detects Windows hosts using two methods (in order):
            1. Host is in a PatchMon group whose name contains 'windows'
            2. Hostname/friendly_name matches common Windows hostname patterns
        - Windows hosts get ansible_connection=winrm and WinRM vars set.
        - Linux hosts get ansible_connection=ssh.
        - All hosts are also placed into auto-groups 'linux_hosts' or 'windows_hosts'
          so playbooks can use group intersection (:&) to target the right OS.
        - Credentials are read from AWX environment variables — do NOT use
          Jinja2 lookup() in the config file, it is not processed there.
    options:
        plugin:
            description: Plugin identifier.
            required: true
            choices:
                - patchmon.dynamic_inventory.dynamic_inventory
                - patchmon.dynamic_inventory
        api_url:
            description: Full URL to the PatchMon hosts API endpoint.
            required: false
            env: [{name: PATCHMON_API_URL}]
        api_key:
            description: API key for HTTP Basic Auth.
            required: false
            env: [{name: PATCHMON_API_KEY}]
        api_secret:
            description: API secret for HTTP Basic Auth.
            required: false
            secret: true
            env: [{name: PATCHMON_API_SECRET}]
        verify_ssl:
            description: Verify SSL certificates.
            type: boolean
            default: false
            env: [{name: PATCHMON_VERIFY_SSL}]
        timeout:
            description: HTTP request timeout in seconds.
            type: integer
            default: 30
            env: [{name: PATCHMON_TIMEOUT}]
        winrm_port:
            description: WinRM HTTPS port for Windows hosts.
            type: integer
            default: 5986
            env: [{name: PATCHMON_WINRM_PORT}]
        winrm_transport:
            description: WinRM transport method.
            default: ntlm
            env: [{name: PATCHMON_WINRM_TRANSPORT}]
        windows_hostname_patterns:
            description: >
                List of case-insensitive substrings. If any match the host's
                friendly_name or hostname, the host is treated as Windows.
                Used as fallback when no 'windows' group is assigned in PatchMon.
            type: list
            elements: string
            default:
                - win
                - ws2
                - dc01
                - dc02
                - "2k8"
                - "2k12"
                - "2k16"
                - "2k19"
                - "2k22"
                - server2
                - msft
"""

EXAMPLES = r"""
# inventory/patchmon_inventory.yml
# Only plugin: is needed — everything else comes from AWX env vars.
plugin:     patchmon.dynamic_inventory.dynamic_inventory
verify_ssl: false
timeout:    30

# Optional: add your own Windows hostname patterns
# windows_hostname_patterns:
#   - win
#   - ws2
#   - mydc
"""

RETURN = r"""# noqa"""

import json
from ansible.errors import AnsibleError, AnsibleParserError
from ansible.plugins.inventory import BaseInventoryPlugin
from ansible.utils.display import Display

display = Display()

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def _detect_windows(host_data, group_names, hostname_patterns):
    """
    Returns (is_windows: bool, reason: str)

    Detection order:
      1. Any assigned PatchMon group name contains 'windows' (case-insensitive)
      2. friendly_name or hostname matches a windows_hostname_patterns entry
      3. Default: Linux
    """
    # Method 1 — group-based (most reliable, user controls this in PatchMon)
    for gname in group_names:
        if "windows" in gname.lower():
            return True, "group '{0}' contains 'windows'".format(gname)

    # Method 2 — hostname pattern matching (fallback)
    friendly = host_data.get("friendly_name", "")
    hostname  = host_data.get("hostname", "")
    check_names = [friendly.lower(), hostname.lower()]

    for pattern in hostname_patterns:
        for name in check_names:
            if pattern.lower() in name:
                return True, "hostname pattern '{0}' matched '{1}'".format(pattern, name or friendly)

    return False, "defaulting to Linux (no Windows indicators found)"


class InventoryModule(BaseInventoryPlugin):
    NAME = "patchmon.dynamic_inventory.dynamic_inventory"

    VALID_SUFFIXES = (
        "patchmon_inventory.yml",
        "patchmon_inventory.yaml",
        "patchmon.yml",
        "patchmon.yaml",
    )

    def verify_file(self, path):
        return super().verify_file(path) and path.endswith(self.VALID_SUFFIXES)

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path, cache)

        if not HAS_REQUESTS:
            raise AnsibleError(
                "PatchMon plugin requires: pip install requests>=2.25.1"
            )

        self._read_config_data(path)

        api_url             = self.get_option("api_url")
        api_key             = self.get_option("api_key")
        api_secret          = self.get_option("api_secret")
        verify_ssl          = self.get_option("verify_ssl")
        timeout             = self.get_option("timeout")
        winrm_port          = self.get_option("winrm_port")
        winrm_transport     = self.get_option("winrm_transport")
        hostname_patterns   = self.get_option("windows_hostname_patterns")

        # ── Validate env vars ─────────────────────────────────────────────────
        missing = [k for k, v in {
            "PATCHMON_API_URL":    api_url,
            "PATCHMON_API_KEY":    api_key,
            "PATCHMON_API_SECRET": api_secret,
        }.items() if not v]

        if missing:
            raise AnsibleError(
                "PatchMon: missing environment variables: {0}\n"
                "Fix: AWX → Inventories → Sources → [source] → "
                "Environment Variables → add the missing vars there.\n"
                "(Variables on the Job Template are NOT available during "
                "inventory sync — they must be on the Source.)".format(
                    ", ".join(missing)
                )
            )

        display.vv("PatchMon: GET {0}".format(api_url))

        # ── HTTP call ─────────────────────────────────────────────────────────
        try:
            resp = requests.get(
                api_url,
                auth=(api_key, api_secret),
                verify=verify_ssl,
                timeout=timeout,
                headers={"Accept": "application/json"},
            )
        except requests.exceptions.SSLError as e:
            raise AnsibleError(
                "PatchMon SSL error — set verify_ssl: false for self-signed certs.\n"
                "Detail: {0}".format(e)
            )
        except requests.exceptions.ConnectionError as e:
            raise AnsibleError(
                "PatchMon: cannot reach {0}.\n"
                "Check the URL is reachable from the AWX execution environment.\n"
                "Detail: {1}".format(api_url, e)
            )
        except requests.exceptions.Timeout:
            raise AnsibleError(
                "PatchMon: request timed out after {0}s.".format(timeout)
            )

        # ── Detect HTML / wrong URL ───────────────────────────────────────────
        content_type = resp.headers.get("Content-Type", "")
        preview      = resp.text[:300].strip()

        if resp.status_code == 401:
            raise AnsibleError(
                "PatchMon: HTTP 401 Unauthorized.\n"
                "Check PATCHMON_API_KEY and PATCHMON_API_SECRET in the "
                "inventory source environment variables.\n"
                "URL: {0}".format(api_url)
            )

        if "text/html" in content_type or "<html" in preview.lower():
            raise AnsibleError(
                "PatchMon: received HTML instead of JSON (HTTP {0}).\n\n"
                "PATCHMON_API_URL must end with /api/v1/api/hosts/\n"
                "Current URL: {1}\n"
                "If the URL looks correct, check that the API credentials "
                "are not expired.".format(resp.status_code, api_url)
            )

        if resp.status_code != 200:
            raise AnsibleError(
                "PatchMon: unexpected HTTP {0} from {1}\n"
                "Body: {2}".format(resp.status_code, api_url, preview)
            )

        # ── Parse JSON ────────────────────────────────────────────────────────
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise AnsibleParserError(
                "PatchMon: non-JSON response.\n"
                "URL: {0}\nPreview: {1}\nError: {2}".format(
                    api_url, preview[:200], e
                )
            )

        hosts = data.get("hosts", [])
        if not hosts:
            display.warning(
                "PatchMon: API returned 0 hosts. "
                "Check API key permissions."
            )
            return

        display.vv("PatchMon: {0} host(s) received".format(len(hosts)))

        # Pre-create OS auto-groups
        self.inventory.add_group("linux_hosts")
        self.inventory.add_group("windows_hosts")

        linux_count   = 0
        windows_count = 0

        for h in hosts:
            # API fields confirmed from actual response:
            #   friendly_name, hostname, ip, id, host_groups[]{id, name}
            friendly_name = h.get("friendly_name", "").strip()
            hostname      = h.get("hostname", "").strip()
            ip            = h.get("ip", "")
            groups        = h.get("host_groups", [])

            # Use friendly_name as the Ansible inventory hostname (what shows in AWX).
            # Fall back to hostname if friendly_name is absent.
            inv_hostname = friendly_name or hostname

            if not inv_hostname:
                display.warning(
                    "PatchMon: skipping host with no name — id: {0}".format(
                        h.get("id", "unknown")
                    )
                )
                continue

            self.inventory.add_host(inv_hostname)

            # ansible_host = IP so Ansible connects to the right address
            if ip:
                self.inventory.set_variable(inv_hostname, "ansible_host", ip)

            # Collect group names for detection logic
            group_names_list = [g.get("name", "") for g in groups if g.get("name")]

            # ── OS detection ──────────────────────────────────────────────────
            is_windows, reason = _detect_windows(h, group_names_list, hostname_patterns)

            display.vvv(
                "PatchMon: {0} ({1}) → {2} [{3}]".format(
                    inv_hostname, ip or "no IP",
                    "Windows" if is_windows else "Linux",
                    reason,
                )
            )

            if is_windows:
                self.inventory.add_child("windows_hosts", inv_hostname)
                self.inventory.set_variable(inv_hostname, "ansible_connection",
                                            "winrm")
                self.inventory.set_variable(inv_hostname, "ansible_port",
                                            winrm_port)
                self.inventory.set_variable(inv_hostname, "ansible_winrm_transport",
                                            winrm_transport)
                self.inventory.set_variable(inv_hostname, "ansible_winrm_server_cert_validation",
                                            "ignore")
                windows_count += 1
            else:
                self.inventory.add_child("linux_hosts", inv_hostname)
                self.inventory.set_variable(inv_hostname, "ansible_connection", "ssh")
                linux_count += 1

            # ── Add to PatchMon host_groups (normalised) ──────────────────────
            for g in groups:
                name = g.get("name", "").strip()
                if name:
                    # Normalise: lowercase, spaces/hyphens → underscores
                    safe = name.lower().replace(" ", "_").replace("-", "_")
                    self.inventory.add_group(safe)
                    self.inventory.add_child(safe, inv_hostname)

        display.v(
            "PatchMon: inventory ready — {0} Linux host(s), "
            "{1} Windows host(s)".format(linux_count, windows_count)
        )
