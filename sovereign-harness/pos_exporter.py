#!/usr/bin/env python3
"""POS Command Center — Prometheus Exporter (POS Edge Edition)

Exposes security and application metrics for POS, the internet-facing
disposable edge node. Watches for intrusion, service health, filesystem
integrity on the SOC stack itself, and network exposure.

NOT the relay exporter. This watches what matters on an internet-facing
box: ingress, brute force, IDS alerts, filesystem tampering, process
anomalies, and service status for the security stack.

System-level metrics (CPU, memory, disk, network bytes) are handled by
node_exporter on :9100. This exporter covers the security/application
layer on :9101.

Background threads tail log files and increment counters.
Polled metrics are updated on collection intervals.
"""

import hashlib
import json
import os
import queue as thread_queue
import re
import subprocess
import sys
import threading
import time

import psutil
from prometheus_client import (
    Counter, Gauge, Info, start_http_server,
    REGISTRY, PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR,
)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False

# ── Configuration ────────────────────────────────────────────────

EXPORTER_PORT = 9101

HOME = os.path.expanduser('~')

EVE_LOG = '/var/log/suricata/eve.json'
AUDIT_LOG = '/var/log/audit/audit.log'
UFW_LOG = '/var/log/ufw.log'
ZEEK_LOG_DIR = '/opt/zeek/logs/'
LYNIS_REPORT = '/var/log/lynis-report.dat'
AUTH_LOG = '/var/log/auth.log'

# POS-specific watch targets: the SOC stack itself, sovereign tools, configs
WATCH_DIRS = [
    os.path.join(HOME, 'Desktop', 'pos-command/'),
    os.path.join(HOME, 'Desktop', 'sovereign_editor/'),
    os.path.join(HOME, 'Desktop', 'sovereign-harness/'),
    '/etc/ssh/',
    '/etc/ufw/',
    '/etc/fail2ban/',
    '/sanctuary/',
]

IGNORE_SEGMENTS = [
    'node_modules', '__pycache__', '.git/objects', '.git/pack',
    'pos-command/backend/venv', 'grafana-stack/venv',
    'grafana-offline/logs',
]

# Critical files on POS: the SOC config, hardening configs, exporter itself
CRITICAL_FILES = [
    os.path.join(HOME, 'Desktop', 'pos-command', 'grafana-stack', 'pos_exporter.py'),
    os.path.join(HOME, 'Desktop', 'pos-command', 'grafana-stack', 'prometheus.yml'),
    os.path.join(HOME, 'Desktop', 'pos-command', 'grafana-stack', 'provisioning', 'dashboards', 'pos-command.json'),
    '/etc/ssh/sshd_config',
    '/etc/ufw/ufw.conf',
    '/etc/fail2ban/jail.d/sshd.conf',
    '/etc/sysctl.d/99-pos.conf',
]

# Dirs where any change is critical
CRITICAL_DIRS = [
    '/etc/ssh/',
    '/etc/fail2ban/',
    '/etc/ufw/',
]

# Auditd keys to flag as critical
CRITICAL_AUDIT_PATHS = [
    '/etc/ssh/',
    '/etc/ufw/',
    '/etc/fail2ban/',
    '/etc/sysctl.d/',
    os.path.join(HOME, 'Desktop', 'pos-command/'),
]

# Processes that matter on POS: security stack, SOC, anything unexpected
PROCESS_FILTER = ['python', 'node', 'grafana', 'prometheus', 'node_exporter',
                  'suricata', 'fail2ban', 'aide', 'zeek', 'ollama', 'nmap',
                  'nc', 'netcat', 'curl', 'wget', 'ssh', 'sshd']

# Processes that should NOT be on POS — flag as suspicious
SUSPICIOUS_PROCESSES = ['cryptominer', 'xmrig', 'cgminer', 'bfgminer',
                        'masscan', 'hydra', 'john', 'hashcat',
                        'reverse_shell', 'meterpreter', 'cobalt']


# ── Helpers ──────────────────────────────────────────────────────

def _which(name):
    try:
        r = subprocess.run(['which', name], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _should_ignore(path):
    for seg in IGNORE_SEGMENTS:
        if seg in path:
            return True
    return path.endswith('.pyc')


def _is_critical(path):
    if path in CRITICAL_FILES:
        return True
    for d in CRITICAL_DIRS:
        if path.startswith(d):
            return True
    return False


def _tail_file(path, use_sudo=False):
    """Open a file for tailing from the end. Returns (file_obj, inode) or None."""
    if not os.path.exists(path):
        return None
    try:
        f = open(path, 'r')
        f.seek(0, 2)
        return f, os.fstat(f.fileno()).st_ino
    except PermissionError:
        if not use_sudo:
            return None
        return 'sudo', path
    except OSError:
        return None


def _readline_or_reopen(f, inode, path):
    """Read a line, reopening on inode change. Returns (line, file, inode)."""
    line = f.readline()
    if line:
        return line.strip(), f, inode
    try:
        new_inode = os.stat(path).st_ino
        if new_inode != inode:
            f.close()
            f = open(path, 'r')
            inode = new_inode
    except OSError:
        pass
    return None, f, inode


# ══════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════

# ── Service Status ───────────────────────────────────────────────

suricata_active = Gauge('pos_suricata_active', 'Suricata IDS running (1=active)')
clamav_active = Gauge('pos_clamav_active', 'ClamAV daemon running (1=active)')
clamav_installed = Gauge('pos_clamav_installed', 'ClamAV installed (1=yes)')
aide_installed = Gauge('pos_aide_installed', 'AIDE installed (1=yes)')
aide_baseline_ready = Gauge('pos_aide_baseline_ready', 'AIDE baseline database ready (1=yes, 0.5=building)')
lynis_installed = Gauge('pos_lynis_installed', 'Lynis installed (1=yes)')
lynis_hardening_index = Gauge('pos_lynis_hardening_index', 'Lynis system hardening index (0-100)')
lynis_warnings = Gauge('pos_lynis_warnings_total', 'Lynis warning count')
lynis_suggestions = Gauge('pos_lynis_suggestions_total', 'Lynis suggestion count')
lynis_tests = Gauge('pos_lynis_tests_executed', 'Lynis tests executed')
zeek_installed = Gauge('pos_zeek_installed', 'Zeek installed (1=yes)')
zeek_active = Gauge('pos_zeek_active', 'Zeek running (1=active)')
zeek_connections = Gauge('pos_zeek_connections_recent', 'Zeek recent connection count')
zeek_bytes = Gauge('pos_zeek_bytes_recent', 'Zeek recent total bytes')
zeek_proto = Gauge('pos_zeek_protocol_connections', 'Zeek connections by protocol', ['proto'])
fail2ban_installed = Gauge('pos_fail2ban_installed', 'Fail2ban installed (1=yes)')
fail2ban_banned = Gauge('pos_fail2ban_banned_current', 'Currently banned IPs', ['jail'])
fail2ban_banned_total = Gauge('pos_fail2ban_banned_cumulative', 'Total bans since service start', ['jail'])

# ── Network (POS edge focus) ────────────────────────────────────

net_connections_total = Gauge('pos_network_connections_total', 'Total active TCP connections')
net_connections_by_state = Gauge('pos_network_connections', 'TCP connections by state', ['state'])
net_listening_ports = Gauge('pos_listening_ports_total', 'Number of listening TCP ports')
net_established_external = Gauge('pos_established_external', 'Established connections to non-loopback')

# ── Event Counters (incremented by background tailers) ───────────

suricata_alerts_total = Counter('pos_suricata_alerts_total', 'Total Suricata alerts observed')
suricata_alerts_by_sev = Counter('pos_suricata_alerts_by_severity', 'Suricata alerts by severity', ['severity'])

auditd_events_total = Counter('pos_auditd_events_total', 'Total auditd events observed')
auditd_critical_total = Counter('pos_auditd_critical_events_total', 'Critical auditd events (hardening paths)')

ufw_blocks_total = Counter('pos_ufw_blocks_total', 'Total UFW block events')
ufw_blocks_by_port = Counter('pos_ufw_blocks_by_port', 'UFW blocks by destination port', ['dport'])

ssh_auth_failures = Counter('pos_ssh_auth_failures_total', 'SSH authentication failures')

fs_events_total = Counter('pos_filesystem_events_total', 'Filesystem events by type', ['event_type'])
fs_critical_events = Counter('pos_filesystem_critical_events_total', 'Critical filesystem events (SOC/hardening)')
fs_hash_changes = Counter('pos_filesystem_hash_changes_total', 'File hash changes detected')

# ── Process Monitoring ───────────────────────────────────────────

proc_cpu = Gauge('pos_process_cpu_percent', 'Process CPU usage', ['name', 'role', 'pid'])
proc_mem = Gauge('pos_process_memory_percent', 'Process memory usage', ['name', 'role', 'pid'])
proc_uptime = Gauge('pos_process_uptime_seconds', 'Process uptime', ['name', 'role', 'pid'])
suspicious_procs = Gauge('pos_suspicious_processes', 'Suspicious processes detected')

# ── Filesystem Integrity ────────────────────────────────────────

monitored_files = Gauge('pos_monitored_files_total', 'Number of files in hash baseline')


# ══════════════════════════════════════════════════════════════════
# COLLECTORS
# ══════════════════════════════════════════════════════════════════

class ServiceStatusCollector(threading.Thread):
    """Polls service status and tool metrics at intervals."""

    daemon = True

    def run(self):
        psutil.cpu_percent(interval=None)
        while True:
            try:
                self._collect_suricata_status()
                self._collect_clamav()
                self._collect_aide()
                self._collect_lynis()
                self._collect_zeek()
                self._collect_fail2ban()
            except Exception as e:
                print(f'[service_status] {e}', file=sys.stderr)
            time.sleep(30)

    def _collect_suricata_status(self):
        try:
            r = subprocess.run(['systemctl', 'is-active', 'suricata'],
                               capture_output=True, text=True, timeout=5)
            suricata_active.set(1 if r.stdout.strip() == 'active' else 0)
        except Exception:
            suricata_active.set(0)

    def _collect_clamav(self):
        installed = _which('clamscan')
        clamav_installed.set(1 if installed else 0)
        if not installed:
            clamav_active.set(0)
            return
        try:
            r = subprocess.run(['systemctl', 'is-active', 'clamav-daemon'],
                               capture_output=True, text=True, timeout=5)
            clamav_active.set(1 if r.stdout.strip() == 'active' else 0)
        except Exception:
            clamav_active.set(0)

    def _collect_aide(self):
        installed = _which('aide')
        aide_installed.set(1 if installed else 0)
        if not installed:
            aide_baseline_ready.set(0)
            return
        try:
            r = subprocess.run(['sudo', 'ls', '-la', '/var/lib/aide/'],
                               capture_output=True, text=True, timeout=5)
            has_db = False
            for line in r.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 5:
                    fname = parts[-1]
                    try:
                        fsize = int(parts[4])
                    except ValueError:
                        fsize = 0
                    if fname == 'aide.db' and fsize > 0:
                        has_db = True
                    elif fname == 'aide.db.new' and fsize > 0:
                        has_db = True
            if has_db:
                aide_baseline_ready.set(1)
            else:
                try:
                    ri = subprocess.run(['pgrep', '-f', 'aideinit|aide.*--init'],
                                        capture_output=True, text=True, timeout=5)
                    aide_baseline_ready.set(0.5 if ri.returncode == 0 else 0)
                except Exception:
                    aide_baseline_ready.set(0)
        except Exception:
            aide_baseline_ready.set(0)

    def _collect_lynis(self):
        installed = _which('lynis')
        lynis_installed.set(1 if installed else 0)
        if not installed:
            lynis_hardening_index.set(0)
            return

        text = ''
        if os.path.exists(LYNIS_REPORT):
            try:
                with open(LYNIS_REPORT, 'r') as f:
                    text = f.read()
            except PermissionError:
                try:
                    r = subprocess.run(['sudo', 'cat', LYNIS_REPORT],
                                       capture_output=True, text=True, timeout=10)
                    text = r.stdout
                except Exception:
                    pass
            except OSError:
                pass

        if not text:
            return

        warn_count = 0
        suggest_count = 0
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('hardening_index='):
                try:
                    lynis_hardening_index.set(int(line.split('=', 1)[1]))
                except ValueError:
                    pass
            elif line.startswith('tests_executed='):
                try:
                    lynis_tests.set(int(line.split('=', 1)[1]))
                except ValueError:
                    pass
            elif line.startswith('warning[]='):
                warn_count += 1
            elif line.startswith('suggestion[]='):
                suggest_count += 1

        lynis_warnings.set(warn_count)
        lynis_suggestions.set(suggest_count)

    def _collect_zeek(self):
        installed = os.path.isdir(ZEEK_LOG_DIR)
        zeek_installed.set(1 if installed else 0)
        if not installed:
            zeek_active.set(0)
            return

        try:
            r = subprocess.run(['systemctl', 'is-active', 'zeek'],
                               capture_output=True, text=True, timeout=5)
            running = r.stdout.strip() == 'active'
            if not running:
                r2 = subprocess.run(['pgrep', '-x', 'zeek'],
                                    capture_output=True, text=True, timeout=5)
                running = r2.returncode == 0
            zeek_active.set(1 if running else 0)
        except Exception:
            zeek_active.set(0)

        conn_log = os.path.join(ZEEK_LOG_DIR, 'current', 'conn.log')
        if not os.path.exists(conn_log):
            zeek_connections.set(0)
            zeek_bytes.set(0)
            return

        try:
            r = subprocess.run(['tail', '-100', conn_log],
                               capture_output=True, text=True, timeout=5)
            count = 0
            total_bytes = 0
            protocols = {}
            for line in r.stdout.strip().split('\n'):
                if line.startswith('#') or not line.strip():
                    continue
                fields = line.split('\t')
                if len(fields) < 7:
                    continue
                count += 1
                proto = fields[6].upper()
                protocols[proto] = protocols.get(proto, 0) + 1
                ob, rb = 0, 0
                try:
                    if len(fields) > 9 and fields[9] != '-':
                        ob = int(fields[9])
                    if len(fields) > 10 and fields[10] != '-':
                        rb = int(fields[10])
                except ValueError:
                    pass
                total_bytes += ob + rb

            zeek_connections.set(count)
            zeek_bytes.set(total_bytes)
            zeek_proto._metrics.clear()
            for proto, cnt in protocols.items():
                zeek_proto.labels(proto=proto).set(cnt)
        except Exception:
            pass

    def _collect_fail2ban(self):
        try:
            r = subprocess.run(['sudo', 'fail2ban-client', 'status'],
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                fail2ban_installed.set(0)
                return
            fail2ban_installed.set(1)

            jail_match = re.search(r'Jail list:\s*(.*)', r.stdout)
            if not jail_match:
                return
            jail_names = [j.strip() for j in jail_match.group(1).split(',') if j.strip()]
            for jail in jail_names:
                try:
                    jr = subprocess.run(['sudo', 'fail2ban-client', 'status', jail],
                                        capture_output=True, text=True, timeout=5)
                    banned = 0
                    total = 0
                    ban_match = re.search(r'Currently banned:\s*(\d+)', jr.stdout)
                    total_match = re.search(r'Total banned:\s*(\d+)', jr.stdout)
                    if ban_match:
                        banned = int(ban_match.group(1))
                    if total_match:
                        total = int(total_match.group(1))
                    fail2ban_banned.labels(jail=jail).set(banned)
                    fail2ban_banned_total.labels(jail=jail).set(total)
                except Exception:
                    pass
        except FileNotFoundError:
            fail2ban_installed.set(0)
        except Exception:
            pass


class NetworkCollector(threading.Thread):
    """Polls network connection info with POS edge focus."""

    daemon = True

    def run(self):
        while True:
            try:
                self._collect_connections()
            except Exception as e:
                print(f'[network] {e}', file=sys.stderr)
            time.sleep(5)

    def _collect_connections(self):
        try:
            r = subprocess.run(['ss', '-tna'], capture_output=True, text=True, timeout=5)
            states = {}
            total = 0
            listening = 0
            external = 0
            for line in r.stdout.strip().split('\n')[1:]:
                parts = line.split()
                if len(parts) < 5:
                    continue
                total += 1
                state = parts[0]
                states[state] = states.get(state, 0) + 1

                # Count listening ports
                if state == 'LISTEN':
                    listening += 1

                # Count established connections to non-loopback
                if state == 'ESTAB':
                    remote = parts[4]
                    if not remote.startswith('127.') and not remote.startswith('[::1]'):
                        external += 1

            net_connections_total.set(total)
            net_listening_ports.set(listening)
            net_established_external.set(external)
            net_connections_by_state._metrics.clear()
            for state, count in states.items():
                net_connections_by_state.labels(state=state).set(count)
        except Exception:
            pass


class ProcessCollector(threading.Thread):
    """Polls process metrics for POS-relevant processes."""

    daemon = True

    def run(self):
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        while True:
            try:
                self._collect()
            except Exception as e:
                print(f'[processes] {e}', file=sys.stderr)
            time.sleep(5)

    def _collect(self):
        proc_cpu._metrics.clear()
        proc_mem._metrics.clear()
        proc_uptime._metrics.clear()

        sus_count = 0
        now = time.time()

        for proc in psutil.process_iter(
            ['pid', 'name', 'cmdline', 'cpu_percent', 'memory_percent', 'create_time']
        ):
            try:
                info = proc.info
                name_lower = (info['name'] or '').lower()
                cmdline_str = ' '.join(info['cmdline'] or []).lower()

                # Check for suspicious processes first
                for sus in SUSPICIOUS_PROCESSES:
                    if sus in name_lower or sus in cmdline_str:
                        sus_count += 1

                matched = any(f in name_lower or f in cmdline_str for f in PROCESS_FILTER)
                if not matched:
                    continue

                # Assign role based on what it is on POS
                role = ''
                if 'grafana' in name_lower or 'grafana' in cmdline_str:
                    role = 'Grafana'
                elif 'prometheus' in name_lower and 'node_exporter' not in cmdline_str:
                    role = 'Prometheus'
                elif 'node_exporter' in name_lower or 'node_exporter' in cmdline_str:
                    role = 'NodeExporter'
                elif 'pos_exporter' in cmdline_str:
                    role = 'PosExporter'
                elif 'suricata' in name_lower:
                    role = 'Suricata'
                elif 'fail2ban' in name_lower:
                    role = 'Fail2ban'
                elif 'aide' in name_lower and 'aide' in cmdline_str:
                    role = 'AIDE'
                elif 'zeek' in name_lower:
                    role = 'Zeek'
                elif name_lower == 'sshd':
                    role = 'SSHd'
                elif name_lower == 'ssh':
                    role = 'SSHClient'
                elif name_lower in ('nmap', 'nc', 'netcat', 'masscan'):
                    role = 'Scanner'
                    sus_count += 1

                pid = str(info['pid'])
                name = info['name'] or 'unknown'
                labels = dict(name=name, role=role, pid=pid)

                proc_cpu.labels(**labels).set(round(info['cpu_percent'] or 0, 1))
                proc_mem.labels(**labels).set(round(info['memory_percent'] or 0, 1))
                if info['create_time']:
                    proc_uptime.labels(**labels).set(int(now - info['create_time']))

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        suspicious_procs.set(sus_count)


class SuricataTailer(threading.Thread):
    """Tails eve.json and increments alert counters."""

    daemon = True

    def run(self):
        while not os.path.exists(EVE_LOG):
            time.sleep(5)

        try:
            f = open(EVE_LOG, 'r')
            f.seek(0, 2)
            inode = os.fstat(f.fileno()).st_ino
        except (OSError, PermissionError):
            print(f'[suricata_tailer] Cannot open {EVE_LOG}', file=sys.stderr)
            return

        while True:
            line, f, inode = _readline_or_reopen(f, inode, EVE_LOG)
            if line:
                try:
                    event = json.loads(line)
                    if event.get('event_type') == 'alert':
                        suricata_alerts_total.inc()
                        severity = str(event.get('alert', {}).get('severity', 0))
                        suricata_alerts_by_sev.labels(severity=severity).inc()
                except (json.JSONDecodeError, KeyError):
                    pass
            else:
                time.sleep(0.5)


class AuditdTailer(threading.Thread):
    """Tails audit.log and increments event counters."""

    daemon = True
    SKIP_TYPES = {'BPF', 'PROCTITLE', 'CWD'}

    def run(self):
        while not os.path.exists(AUDIT_LOG):
            time.sleep(5)

        try:
            f = open(AUDIT_LOG, 'r')
            f.seek(0, 2)
            inode = os.fstat(f.fileno()).st_ino
            self._tail_direct(f, inode)
        except PermissionError:
            self._tail_sudo()
        except OSError:
            print(f'[auditd_tailer] Cannot open {AUDIT_LOG}', file=sys.stderr)

    def _tail_direct(self, f, inode):
        while True:
            line, f, inode = _readline_or_reopen(f, inode, AUDIT_LOG)
            if line:
                self._process_line(line)
            else:
                time.sleep(0.5)

    def _tail_sudo(self):
        try:
            proc = subprocess.Popen(
                ['sudo', 'tail', '-f', '-n', '0', AUDIT_LOG],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self._process_line(line)
        except Exception as e:
            print(f'[auditd_tailer] sudo fallback failed: {e}', file=sys.stderr)

    def _process_line(self, line):
        type_match = re.match(r'type=(\S+)', line)
        if not type_match:
            return
        event_type = type_match.group(1)
        if event_type in self.SKIP_TYPES:
            return

        auditd_events_total.inc()

        is_critical = False
        for field_re in [r'name="([^"]*)"', r'cwd="([^"]*)"', r'exe="([^"]*)"']:
            m = re.search(field_re, line)
            if m:
                val = m.group(1)
                if any(val.startswith(cp) for cp in CRITICAL_AUDIT_PATHS):
                    is_critical = True
                    break

        key_match = re.search(r'key="?([^"\s]+)"?', line)
        if key_match:
            key_val = key_match.group(1).strip('(').rstrip(')')
            if key_val in ('pos_soc', 'pos_hardening', 'pos_config'):
                is_critical = True

        if is_critical:
            auditd_critical_total.inc()


class UFWTailer(threading.Thread):
    """Tails ufw.log, counts block events, and tracks blocked ports."""

    daemon = True

    def run(self):
        if not os.path.exists(UFW_LOG):
            return

        try:
            f = open(UFW_LOG, 'r')
            f.seek(0, 2)
            inode = os.fstat(f.fileno()).st_ino
        except (OSError, PermissionError):
            return

        while True:
            line, f, inode = _readline_or_reopen(f, inode, UFW_LOG)
            if line:
                if '[UFW BLOCK]' in line:
                    ufw_blocks_total.inc()
                    # Extract destination port
                    dpt_match = re.search(r'DPT=(\d+)', line)
                    if dpt_match:
                        ufw_blocks_by_port.labels(dport=dpt_match.group(1)).inc()
            else:
                time.sleep(0.5)


class SSHAuthTailer(threading.Thread):
    """Tails auth.log for SSH authentication failures."""

    daemon = True

    def run(self):
        if not os.path.exists(AUTH_LOG):
            return

        try:
            f = open(AUTH_LOG, 'r')
            f.seek(0, 2)
            inode = os.fstat(f.fileno()).st_ino
        except (OSError, PermissionError):
            # Try sudo fallback
            try:
                proc = subprocess.Popen(
                    ['sudo', 'tail', '-f', '-n', '0', AUTH_LOG],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True,
                )
                for line in proc.stdout:
                    line = line.strip()
                    if line and ('Failed password' in line or 'authentication failure' in line
                                or 'Invalid user' in line):
                        ssh_auth_failures.inc()
            except Exception:
                pass
            return

        while True:
            line, f, inode = _readline_or_reopen(f, inode, AUTH_LOG)
            if line:
                if ('Failed password' in line or 'authentication failure' in line
                        or 'Invalid user' in line):
                    ssh_auth_failures.inc()
            else:
                time.sleep(0.5)


class FilesystemWatcher(threading.Thread):
    """Watches POS SOC directories with inotify and counts events.
    Also periodically checks critical file hashes."""

    daemon = True

    def __init__(self):
        super().__init__()
        self._baseline_hashes = {}
        self._compute_baseline()

    def _compute_baseline(self):
        for fpath in CRITICAL_FILES:
            if os.path.exists(fpath):
                try:
                    h = hashlib.sha256(open(fpath, 'rb').read()).hexdigest()
                    self._baseline_hashes[fpath] = h
                except (OSError, PermissionError):
                    pass
        # Also walk critical dirs for additional files
        for d in CRITICAL_DIRS:
            if not os.path.isdir(d):
                continue
            for root, dirs, files in os.walk(d):
                dirs[:] = [dd for dd in dirs if not _should_ignore(os.path.join(root, dd))]
                for f in files:
                    full = os.path.join(root, f)
                    if not _should_ignore(full):
                        try:
                            h = hashlib.sha256(open(full, 'rb').read()).hexdigest()
                            self._baseline_hashes[full] = h
                        except (OSError, PermissionError):
                            pass
        monitored_files.set(len(self._baseline_hashes))

    def run(self):
        if HAS_WATCHDOG:
            self._run_watchdog()
        else:
            while True:
                self._check_hashes()
                time.sleep(60)

    def _run_watchdog(self):
        eq = thread_queue.Queue()
        handler = _WatchdogHandler(eq)
        observer = Observer()
        observer.daemon = True
        for d in WATCH_DIRS:
            if os.path.isdir(d):
                try:
                    observer.schedule(handler, d, recursive=True)
                except OSError:
                    pass
        observer.start()

        last_hash_check = time.time()
        while True:
            try:
                while True:
                    event = eq.get_nowait()
                    event_type = event.get('event', 'unknown')
                    fs_events_total.labels(event_type=event_type).inc()
                    if event.get('priority') == 'critical':
                        fs_critical_events.inc()
            except thread_queue.Empty:
                pass

            if time.time() - last_hash_check > 60:
                self._check_hashes()
                last_hash_check = time.time()

            time.sleep(0.5)

    def _check_hashes(self):
        for fpath, baseline in list(self._baseline_hashes.items()):
            try:
                current = hashlib.sha256(open(fpath, 'rb').read()).hexdigest()
                if current != baseline:
                    fs_hash_changes.inc()
                    self._baseline_hashes[fpath] = current
            except FileNotFoundError:
                fs_hash_changes.inc()
            except (OSError, PermissionError):
                pass
        monitored_files.set(len(self._baseline_hashes))


if HAS_WATCHDOG:
    class _WatchdogHandler(FileSystemEventHandler):
        def __init__(self, eq):
            self._q = eq

        def _emit(self, event_type, path, **kwargs):
            if _should_ignore(path):
                return
            priority = 'critical' if _is_critical(path) else 'normal'
            self._q.put({'event': event_type, 'path': path, 'priority': priority})

        def on_created(self, event):
            if not event.is_directory:
                self._emit('created', event.src_path)

        def on_modified(self, event):
            if not event.is_directory:
                self._emit('modified', event.src_path)

        def on_deleted(self, event):
            if not event.is_directory:
                self._emit('deleted', event.src_path)

        def on_moved(self, event):
            if not event.is_directory:
                self._emit('moved', event.src_path)


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    for c in [PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR]:
        try:
            REGISTRY.unregister(c)
        except Exception:
            pass

    print(f'POS Exporter (Edge Edition) starting on :{EXPORTER_PORT}')
    start_http_server(EXPORTER_PORT)

    threads = [
        ServiceStatusCollector(),
        NetworkCollector(),
        ProcessCollector(),
        SuricataTailer(),
        AuditdTailer(),
        UFWTailer(),
        SSHAuthTailer(),
        FilesystemWatcher(),
    ]
    for t in threads:
        t.start()
        print(f'  Started {t.__class__.__name__}')

    print('All collectors running. Metrics at http://localhost:9101/metrics')
    sys.stdout.flush()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
