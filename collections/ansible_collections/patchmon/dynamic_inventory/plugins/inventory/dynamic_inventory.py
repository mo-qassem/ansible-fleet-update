# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
    name: dynamic_inventory
    plugin_type: inventory
    short_description: PatchMon dynamic inventory plugin
    description:
        - Queries the PatchMon REST API and builds an Ansible inventory.
        - Uses PatchMon API os_type field to classify hosts.
        - Windows hosts are placed only in windows_hosts.
        - Linux hosts are placed only in linux_hosts.
        - Hosts with missing or unknown os_type are placed in unknown_os_hosts.
        - PatchMon host_groups such as Basic, Advanced, Professional are saved
          as host variables only, not created as Ansible inventory groups.
        - Credentials are read from AWX environment variables.
        - Do NOT use Jinja2 lookup() in the inventory config file because AWX
          inventory source sync does not process it the same way as a playbook.
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
        winrm_server_cert_validation:
            description: WinRM certificate validation mode.
            default: ignore
            env: [{name: PATCHMON_WINRM_CERT_VALIDATION}]
"""

EXAMPLES = r"""
# inventory/patchmon_inventory.yml

plugin: patchmon.dynamic_inventory.dynamic_inventory
verify_ssl: false
timeout: 30
winrm_port: 5986
winrm_transport: ntlm
winrm_server_cert_validation: ignore

# API credentials should be added in AWX Inventory Source Environment Variables:
#
# PATCHMON_API_URL: "https://patchmon.example.com/api/v1/api/hosts/"
# PATCHMON_API_KEY: "your_api_key"
# PATCHMON_API_SECRET: "your_api_secret"
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


def _detect_os_type(host_data):
    """
    Detect OS using PatchMon API os_type.

    Returns:
      ("windows", reason)
      ("linux", reason)
      ("unknown", reason)

    Expected PatchMon examples:
      os_type: Windows
      os_type: Linux
      os_type: Ubuntu
      os_type: CentOS
      os_type: Rocky Linux
      os_type: AlmaLinux
      os_type: CloudLinux
    """

    os_type_raw = host_data.get("os_type", "")
    os_type = str(os_type_raw or "").strip().lower()

    if not os_type:
        return "unknown", "missing os_type from PatchMon API"

    if "windows" in os_type:
        return "windows", "os_type='{0}'".format(os_type_raw)

    linux_indicators = [
        "linux",
        "ubuntu",
        "debian",
        "centos",
        "rocky",
        "alma",
        "almalinux",
        "cloudlinux",
        "redhat",
        "red hat",
        "rhel",
        "oracle linux",
        "freebsd",
        "unix",
    ]

    for indicator in linux_indicators:
        if indicator in os_type:
            return "linux", "os_type='{0}'".format(os_type_raw)

    return "unknown", "unrecognized os_type='{0}'".format(os_type_raw)


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
                "PatchMon plugin requires Python requests library. "
                "Install it with: pip install requests>=2.25.1"
            )

        self._read_config_data(path)

        api_url = self.get_option("api_url")
        api_key = self.get_option("api_key")
        api_secret = self.get_option("api_secret")
        verify_ssl = self.get_option("verify_ssl")
        timeout = self.get_option("timeout")
        winrm_port = self.get_option("winrm_port")
        winrm_transport = self.get_option("winrm_transport")
        winrm_cert_validation = self.get_option("winrm_server_cert_validation")

        missing = [
            key for key, value in {
                "PATCHMON_API_URL": api_url,
                "PATCHMON_API_KEY": api_key,
                "PATCHMON_API_SECRET": api_secret,
            }.items()
            if not value
        ]

        if missing:
            raise AnsibleError(
                "PatchMon: missing environment variables: {0}\n"
                "Fix in AWX:\n"
                "Inventory → Sources → PatchMon source → Environment Variables.\n"
                "Important: Job Template extra vars are not available during "
                "inventory sync.".format(", ".join(missing))
            )

        display.vv("PatchMon: GET {0}".format(api_url))

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
                "PatchMon SSL error. If using a self-signed certificate, set "
                "verify_ssl: false in inventory/patchmon_inventory.yml.\n"
                "Detail: {0}".format(e)
            )
        except requests.exceptions.ConnectionError as e:
            raise AnsibleError(
                "PatchMon: cannot reach {0} from the AWX execution environment.\n"
                "Detail: {1}".format(api_url, e)
            )
        except requests.exceptions.Timeout:
            raise AnsibleError(
                "PatchMon: request timed out after {0} seconds.".format(timeout)
            )

        content_type = resp.headers.get("Content-Type", "")
        preview = resp.text[:300].strip()

        if resp.status_code == 401:
            raise AnsibleError(
                "PatchMon: HTTP 401 Unauthorized.\n"
                "Check PATCHMON_API_KEY and PATCHMON_API_SECRET in the AWX "
                "inventory source environment variables.\n"
                "URL: {0}".format(api_url)
            )

        if "text/html" in content_type or "<html" in preview.lower():
            raise AnsibleError(
                "PatchMon: received HTML instead of JSON. HTTP {0}\n\n"
                "PATCHMON_API_URL must point to the hosts API endpoint, for example:\n"
                "https://patchmon.example.com/api/v1/api/hosts/\n\n"
                "Current URL: {1}\n"
                "Preview: {2}".format(resp.status_code, api_url, preview[:200])
            )

        if resp.status_code != 200:
            raise AnsibleError(
                "PatchMon: unexpected HTTP {0} from {1}\n"
                "Body preview: {2}".format(resp.status_code, api_url, preview)
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise AnsibleParserError(
                "PatchMon: non-JSON response.\n"
                "URL: {0}\n"
                "Preview: {1}\n"
                "Error: {2}".format(api_url, preview[:200], e)
            )

        hosts = data.get("hosts", [])

        if not hosts:
            display.warning(
                "PatchMon: API returned 0 hosts. Check API URL and API key permissions."
            )
            return

        display.vv("PatchMon: {0} host(s) received".format(len(hosts)))

        # Only these groups will be created for targeting playbooks.
        self.inventory.add_group("linux_hosts")
        self.inventory.add_group("windows_hosts")
        self.inventory.add_group("unknown_os_hosts")

        linux_count = 0
        windows_count = 0
        unknown_count = 0

        for h in hosts:
            friendly_name = str(h.get("friendly_name", "") or "").strip()
            hostname = str(h.get("hostname", "") or "").strip()
            ip = str(h.get("ip", "") or "").strip()
            host_id = str(h.get("id", "") or "").strip()

            # Friendly name is what will appear in AWX.
            # Fallback order prevents skipping hosts if friendly_name is empty.
            inv_hostname = friendly_name or hostname or host_id

            if not inv_hostname:
                display.warning("PatchMon: skipping host with no friendly_name, hostname, or id")
                continue

            self.inventory.add_host(inv_hostname)

            if ip:
                self.inventory.set_variable(inv_hostname, "ansible_host", ip)

            # Keep useful PatchMon metadata as host vars.
            self.inventory.set_variable(inv_hostname, "patchmon_id", h.get("id"))
            self.inventory.set_variable(inv_hostname, "patchmon_hostname", hostname)
            self.inventory.set_variable(inv_hostname, "patchmon_friendly_name", friendly_name)
            self.inventory.set_variable(inv_hostname, "patchmon_os_type", h.get("os_type"))
            self.inventory.set_variable(inv_hostname, "patchmon_needs_reboot", h.get("needs_reboot"))
            self.inventory.set_variable(inv_hostname, "patchmon_last_update", h.get("last_update"))

            # Keep Basic/Advanced/Professional as a variable only.
            # We do not create Ansible groups from these.
            patchmon_groups = []

            for g in h.get("host_groups", []) or []:
                group_name = str(g.get("name", "") or "").strip()
                if group_name:
                    patchmon_groups.append(group_name)

            self.inventory.set_variable(inv_hostname, "patchmon_host_groups", patchmon_groups)

            os_family, reason = _detect_os_type(h)

            display.vvv(
                "PatchMon: {0} ({1}) os_type={2} groups={3} -> {4} [{5}]".format(
                    inv_hostname,
                    ip or "no IP",
                    h.get("os_type"),
                    ",".join(patchmon_groups) if patchmon_groups else "no_groups",
                    os_family,
                    reason,
                )
            )

            if os_family == "windows":
                self.inventory.add_child("windows_hosts", inv_hostname)

                self.inventory.set_variable(inv_hostname, "ansible_connection", "winrm")
                self.inventory.set_variable(inv_hostname, "ansible_port", winrm_port)
                self.inventory.set_variable(inv_hostname, "ansible_winrm_transport", winrm_transport)
                self.inventory.set_variable(
                    inv_hostname,
                    "ansible_winrm_server_cert_validation",
                    winrm_cert_validation,
                )

                windows_count += 1

            elif os_family == "linux":
                self.inventory.add_child("linux_hosts", inv_hostname)

                self.inventory.set_variable(inv_hostname, "ansible_connection", "ssh")

                linux_count += 1

            else:
                self.inventory.add_child("unknown_os_hosts", inv_hostname)

                # Do not assume Windows or Linux when os_type is missing/unknown.
                # Keep ansible_connection unset so it does not accidentally run
                # under the wrong connection type.
                unknown_count += 1

                display.warning(
                    "PatchMon: host {0} has unknown os_type '{1}'. "
                    "Added to unknown_os_hosts only.".format(
                        inv_hostname,
                        h.get("os_type"),
                    )
                )

        display.v(
            "PatchMon: inventory ready — {0} Linux host(s), {1} Windows host(s), "
            "{2} unknown host(s)".format(
                linux_count,
                windows_count,
                unknown_count,
            )
        )