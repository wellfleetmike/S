#!/bin/bash
# ================================================================
#   POS SOVEREIGN AUTO-INSTALLER
#   Adapted from Ember's proven 15-stage INSTALL.sh
#   Target: GTX 1060 / Ubuntu Desktop 24.04 / SOC focus
#   No internet required. No dpkg for Grafana/Prometheus.
#   Standalone tarballs only — zero dependency chains.
#
#   Expected layout next to this script:
#     tarballs/grafana-*.tar.gz
#     tarballs/prometheus-*.tar.gz
#     tarballs/node_exporter-*.tar.gz
#     configs/sysctl_hardening.conf
#     configs/sshd_config_sovereign
#     configs/fail2ban_sshd.conf
#     configs/ufw_rules.sh
#     pos-command/          (SOC tree with grafana-offline/, grafana-stack/)
#     drivers/NVIDIA-Linux-x86_64-*.run   (optional)
#
#   Run: sudo ./INSTALL_POS.sh   (resumable via /tmp/pos_install_stage)
# ================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="/var/log/pos_sovereign_install.log"
PROGRESS="/tmp/pos_install_stage"
OPERATOR="mike"
OPERATOR_HOME="/home/$OPERATOR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${GREEN}[POS]${NC} $1" | tee -a "$LOG"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG"; }
err()  { echo -e "${RED}[FAIL]${NC} $1" | tee -a "$LOG"; exit 1; }
info() { echo -e "${CYAN}[INFO]${NC} $1" | tee -a "$LOG"; }

stage() { cat "$PROGRESS" 2>/dev/null || echo "0"; }
set_stage() { echo "$1" > "$PROGRESS"; }

[ "$EUID" -eq 0 ] || err "Run as root: sudo ./INSTALL_POS.sh"

echo "" | tee -a "$LOG"
echo "================================================================" | tee -a "$LOG"
echo "   POS SOVEREIGN INSTALLER" | tee -a "$LOG"
echo "   Adapted from Ember baseline (proven)" | tee -a "$LOG"
echo "   $(date)" | tee -a "$LOG"
echo "================================================================" | tee -a "$LOG"
echo ""

CURRENT=$(stage)
info "Resuming from stage: $CURRENT"

# ================================================================
# STAGE 1: Base packages (from local debs/ if present, else skip)
# ================================================================
if [ "$CURRENT" -lt 1 ]; then
    log "Stage 1: Base packages..."

    if [ -d "$SCRIPT_DIR/debs" ] && ls "$SCRIPT_DIR/debs/"*.deb &>/dev/null; then
        echo "deb [trusted=yes] file://$SCRIPT_DIR/debs ./" > /etc/apt/sources.list.d/sovereign-local.list

        if [ ! -f "$SCRIPT_DIR/debs/Packages.gz" ] && [ ! -f "$SCRIPT_DIR/debs/Packages" ]; then
            log "Generating package index..."
            cd "$SCRIPT_DIR/debs" && dpkg-scanpackages . /dev/null | gzip -9c > Packages.gz
            cd "$SCRIPT_DIR"
        fi

        apt-get update -o Dir::Etc::sourcelist="/etc/apt/sources.list.d/sovereign-local.list" \
                       -o Dir::Etc::sourceparts="-" 2>>"$LOG" || true

        DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::="--force-confold" --allow-downgrades \
            build-essential python3 python3-pip python3-venv \
            git tmux htop openssh-server ufw fail2ban curl wget net-tools \
            2>>"$LOG" || warn "Some packages failed — may already be installed."

        rm -f /etc/apt/sources.list.d/sovereign-local.list
        log "Base packages done."
    else
        log "No debs/ directory — assuming packages already present from Ubuntu install."
    fi

    set_stage 1
    CURRENT=1
fi

# ================================================================
# STAGE 2: Hardening (sysctl, SSH, UFW, fail2ban)
# ================================================================
if [ "$CURRENT" -lt 2 ]; then
    log "Stage 2: Hardening..."

    # Sysctl
    if [ -f "$SCRIPT_DIR/configs/sysctl_hardening.conf" ]; then
        cp "$SCRIPT_DIR/configs/sysctl_hardening.conf" /etc/sysctl.d/99-pos.conf
        sysctl --system >/dev/null 2>&1 || true
        log "Sysctl hardening applied."
    fi

    # SSH
    if [ -f "$SCRIPT_DIR/configs/sshd_config_sovereign" ]; then
        cp /etc/ssh/sshd_config "/etc/ssh/sshd_config.bak.$(date +%s)" 2>/dev/null || true
        cp "$SCRIPT_DIR/configs/sshd_config_sovereign" /etc/ssh/sshd_config
        log "SSH hardened."
    fi
    mkdir -p "$OPERATOR_HOME/.ssh"
    chmod 700 "$OPERATOR_HOME/.ssh"
    if [ -f "$SCRIPT_DIR/configs/authorized_keys_operator" ]; then
        cp "$SCRIPT_DIR/configs/authorized_keys_operator" "$OPERATOR_HOME/.ssh/authorized_keys"
        chmod 600 "$OPERATOR_HOME/.ssh/authorized_keys"
    fi
    chown -R "$OPERATOR:$OPERATOR" "$OPERATOR_HOME/.ssh"

    # Fail2ban
    if [ -f "$SCRIPT_DIR/configs/fail2ban_sshd.conf" ]; then
        mkdir -p /etc/fail2ban/jail.d
        cp "$SCRIPT_DIR/configs/fail2ban_sshd.conf" /etc/fail2ban/jail.d/sshd.conf
    fi

    # UFW
    if [ -f "$SCRIPT_DIR/configs/ufw_rules.sh" ] && command -v ufw &>/dev/null; then
        bash "$SCRIPT_DIR/configs/ufw_rules.sh" >>"$LOG" 2>&1 || warn "ufw script had issues"
        ufw allow from 127.0.0.1 to any port 3000 proto tcp comment "Grafana local" 2>/dev/null || true
        ufw allow from 127.0.0.1 to any port 9090 proto tcp comment "Prometheus local" 2>/dev/null || true
        ufw allow from 127.0.0.1 to any port 9100 proto tcp comment "node_exporter local" 2>/dev/null || true
        ufw allow from 127.0.0.1 to any port 9101 proto tcp comment "pos_exporter local" 2>/dev/null || true
    fi

    systemctl enable --now ufw fail2ban ssh 2>/dev/null || true
    systemctl restart ssh fail2ban 2>/dev/null || true

    # Disable auto-updates and telemetry
    cat > /etc/apt/apt.conf.d/20auto-upgrades << 'EOF'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
EOF
    systemctl disable apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
    systemctl stop apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
    systemctl disable apport.service whoopsie.service 2>/dev/null || true
    systemctl stop apport.service whoopsie.service 2>/dev/null || true

    log "Hardening complete."
    set_stage 2
    CURRENT=2
fi

# ================================================================
# STAGE 3: GPU (GTX 1060, optional)
# ================================================================
if [ "$CURRENT" -lt 3 ]; then
    log "Stage 3: GPU..."

    DRIVER=$(find "$SCRIPT_DIR/drivers" -name "NVIDIA-Linux-x86_64-*.run" -type f 2>/dev/null | head -1)

    if [ -n "$DRIVER" ]; then
        # Blacklist nouveau
        echo "blacklist nouveau" > /etc/modprobe.d/blacklist-nouveau.conf
        echo "options nouveau modeset=0" >> /etc/modprobe.d/blacklist-nouveau.conf
        update-initramfs -u 2>>"$LOG" || warn "initramfs update failed"

        if lsmod | grep -q nouveau; then
            warn "Nouveau still loaded — driver install may need reboot first."
            warn "Reboot, then re-run this script to resume from stage 3."
            set_stage 2
            exit 0
        fi

        # Stop display manager before NVIDIA install
        DM_ACTIVE=""
        for dm in gdm3 gdm lightdm sddm; do
            if systemctl is-active --quiet "$dm" 2>/dev/null; then
                DM_ACTIVE="$dm"
                break
            fi
        done
        if [ -n "$DM_ACTIVE" ]; then
            log "Stopping $DM_ACTIVE..."
            systemctl stop "$DM_ACTIVE" 2>>"$LOG" || true
            sleep 2
        fi

        chmod +x "$DRIVER"
        "$DRIVER" --silent --dkms --no-questions 2>>"$LOG" || warn "NVIDIA install had warnings"

        if [ -n "$DM_ACTIVE" ]; then
            systemctl start "$DM_ACTIVE" 2>>"$LOG" || true
        fi

        log "NVIDIA driver installed."
    else
        log "No NVIDIA .run in drivers/ — skipping GPU. Fine for SOC-only."
    fi

    set_stage 3
    CURRENT=3
fi

# ================================================================
# STAGE 4: SOC — Grafana + Prometheus + node_exporter from tarballs
#          No dpkg, no apt, no musl. Standalone binaries.
# ================================================================
if [ "$CURRENT" -lt 4 ]; then
    log "Stage 4: SOC stack (standalone tarballs)..."

    SOC_DIR="/opt/pos-soc"
    mkdir -p "$SOC_DIR"

    # --- Grafana ---
    GRAFANA_TAR=$(find "$SCRIPT_DIR/tarballs" -name "grafana*linux_amd64.tar.gz" -o -name "grafana*linux-amd64.tar.gz" 2>/dev/null | head -1)
    if [ -z "$GRAFANA_TAR" ]; then
        GRAFANA_TAR=$(find "$SCRIPT_DIR" -maxdepth 1 -name "grafana*.tar.gz" 2>/dev/null | head -1)
    fi
    if [ -n "$GRAFANA_TAR" ]; then
        log "Extracting Grafana from $(basename "$GRAFANA_TAR")..."
        tar xzf "$GRAFANA_TAR" -C "$SOC_DIR"
        GRAFANA_DIR=$(find "$SOC_DIR" -maxdepth 1 -type d -name "grafana*" | head -1)

        # Wire in the existing grafana-offline config and data if present
        OFFLINE_DIR=""
        [ -d "$SCRIPT_DIR/pos-command/grafana-offline" ] && OFFLINE_DIR="$SCRIPT_DIR/pos-command/grafana-offline"
        [ -d "$OPERATOR_HOME/Desktop/grafana-offline" ] && OFFLINE_DIR="$OPERATOR_HOME/Desktop/grafana-offline"
        [ -d "$OPERATOR_HOME/Desktop/pos-command/grafana-offline" ] && OFFLINE_DIR="$OPERATOR_HOME/Desktop/pos-command/grafana-offline"

        if [ -n "$OFFLINE_DIR" ] && [ -n "$GRAFANA_DIR" ]; then
            # Copy provisioning (dashboards + datasources)
            if [ -d "$OFFLINE_DIR/provisioning" ]; then
                cp -r "$OFFLINE_DIR/provisioning/"* "$GRAFANA_DIR/conf/provisioning/" 2>/dev/null || true
                log "Provisioning (dashboards + datasources) installed from grafana-offline."
            fi
            # Copy grafana.db (dashboard state, users, etc)
            if [ -f "$OFFLINE_DIR/data/grafana.db" ]; then
                mkdir -p "$GRAFANA_DIR/data"
                cp "$OFFLINE_DIR/data/grafana.db" "$GRAFANA_DIR/data/grafana.db"
                log "grafana.db restored — your panels are back."
            fi
        fi

        # Systemd unit for Grafana
        cat > /etc/systemd/system/grafana-server.service << GEOF
[Unit]
Description=Grafana Server (standalone)
After=network.target

[Service]
Type=simple
User=$OPERATOR
ExecStart=$GRAFANA_DIR/bin/grafana server --homepath=$GRAFANA_DIR --config=$GRAFANA_DIR/conf/defaults.ini
Restart=on-failure
WorkingDirectory=$GRAFANA_DIR

[Install]
WantedBy=multi-user.target
GEOF
        chown -R "$OPERATOR:$OPERATOR" "$GRAFANA_DIR"
        systemctl daemon-reload
        systemctl enable --now grafana-server
        log "Grafana installed and running on :3000"
    else
        warn "No Grafana tarball found in tarballs/ — skipping."
    fi

    # --- Prometheus ---
    PROM_TAR=$(find "$SCRIPT_DIR/tarballs" -name "prometheus-*.tar.gz" 2>/dev/null | head -1)
    if [ -z "$PROM_TAR" ]; then
        PROM_TAR=$(find "$SCRIPT_DIR" -maxdepth 1 -name "prometheus-*.tar.gz" 2>/dev/null | head -1)
    fi
    if [ -n "$PROM_TAR" ]; then
        log "Extracting Prometheus from $(basename "$PROM_TAR")..."
        tar xzf "$PROM_TAR" -C "$SOC_DIR"
        PROM_DIR=$(find "$SOC_DIR" -maxdepth 1 -type d -name "prometheus-*" | head -1)

        # Use existing prometheus.yml if available, else create minimal
        PROM_CONF=""
        [ -f "$SCRIPT_DIR/pos-command/grafana-stack/prometheus.yml" ] && PROM_CONF="$SCRIPT_DIR/pos-command/grafana-stack/prometheus.yml"
        [ -f "$SCRIPT_DIR/pos-command/grafana-offline/provisioning/datasources/prometheus.yml" ] && true  # datasource, not scrape config
        [ -f "$OPERATOR_HOME/Desktop/pos-command/grafana-stack/prometheus.yml" ] && PROM_CONF="$OPERATOR_HOME/Desktop/pos-command/grafana-stack/prometheus.yml"
        [ -f "$OPERATOR_HOME/Desktop/prometheus.yml" ] && PROM_CONF="$OPERATOR_HOME/Desktop/prometheus.yml"

        if [ -n "$PROM_CONF" ]; then
            cp "$PROM_CONF" "$PROM_DIR/prometheus.yml"
            log "prometheus.yml copied from existing config."
        else
            cat > "$PROM_DIR/prometheus.yml" << 'PYML'
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
  - job_name: 'pos_exporter'
    static_configs:
      - targets: ['localhost:9101']
PYML
            log "Created default prometheus.yml (node:9100 + pos_exporter:9101)."
        fi

        mkdir -p /var/lib/prometheus
        chown "$OPERATOR:$OPERATOR" /var/lib/prometheus

        cat > /etc/systemd/system/prometheus.service << PEOF
[Unit]
Description=Prometheus (standalone)
After=network.target

[Service]
Type=simple
User=$OPERATOR
ExecStart=$PROM_DIR/prometheus --config.file=$PROM_DIR/prometheus.yml --storage.tsdb.path=/var/lib/prometheus
Restart=on-failure
WorkingDirectory=$PROM_DIR

[Install]
WantedBy=multi-user.target
PEOF
        chown -R "$OPERATOR:$OPERATOR" "$PROM_DIR"
        systemctl daemon-reload
        systemctl enable --now prometheus
        log "Prometheus installed and running on :9090"
    else
        warn "No Prometheus tarball found — skipping."
    fi

    # --- Node Exporter ---
    NE_TAR=$(find "$SCRIPT_DIR/tarballs" -name "node_exporter-*.tar.gz" 2>/dev/null | head -1)
    if [ -z "$NE_TAR" ]; then
        NE_TAR=$(find "$SCRIPT_DIR" -maxdepth 1 -name "node_exporter-*.tar.gz" 2>/dev/null | head -1)
    fi
    if [ -n "$NE_TAR" ]; then
        log "Extracting node_exporter from $(basename "$NE_TAR")..."
        tar xzf "$NE_TAR" -C "$SOC_DIR"
        NE_DIR=$(find "$SOC_DIR" -maxdepth 1 -type d -name "node_exporter-*" | head -1)

        cat > /etc/systemd/system/node_exporter.service << NEOF
[Unit]
Description=Node Exporter (standalone)
After=network.target

[Service]
Type=simple
User=$OPERATOR
ExecStart=$NE_DIR/node_exporter
Restart=on-failure

[Install]
WantedBy=multi-user.target
NEOF
        chown -R "$OPERATOR:$OPERATOR" "$NE_DIR"
        systemctl daemon-reload
        systemctl enable --now node_exporter
        log "node_exporter installed and running on :9100"
    else
        warn "No node_exporter tarball found — skipping."
    fi

    set_stage 4
    CURRENT=4
fi

# ================================================================
# STAGE 5: Deploy pos-command tree + sovereign tools
# ================================================================
if [ "$CURRENT" -lt 5 ]; then
    log "Stage 5: Deploying pos-command + sovereign tools..."

    mkdir -p "$OPERATOR_HOME/Desktop"

    # pos-command (SOC collectors, exporter, grafana-stack config)
    if [ -d "$SCRIPT_DIR/pos-command" ] && [ ! -d "$OPERATOR_HOME/Desktop/pos-command" ]; then
        cp -r "$SCRIPT_DIR/pos-command" "$OPERATOR_HOME/Desktop/pos-command"
        log "pos-command deployed to Desktop."
    fi

    # sovereign_editor
    if [ -d "$SCRIPT_DIR/sovereign_editor" ]; then
        cp -a "$SCRIPT_DIR/sovereign_editor" "$OPERATOR_HOME/Desktop/"
        log "sovereign_editor deployed."
    fi

    # sovereign-harness
    if [ -d "$SCRIPT_DIR/sovereign-harness" ]; then
        cp -a "$SCRIPT_DIR/sovereign-harness" "$OPERATOR_HOME/Desktop/"
        log "sovereign-harness deployed."
    fi

    # pos_exporter venv
    if [ -f "$OPERATOR_HOME/Desktop/pos-command/grafana-stack/pos_exporter.py" ]; then
        cd "$OPERATOR_HOME/Desktop/pos-command/grafana-stack"
        if [ ! -d "venv" ]; then
            python3 -m venv venv 2>/dev/null || warn "Could not create exporter venv"
            if [ -d "venv" ]; then
                source venv/bin/activate
                pip install psutil prometheus_client watchdog 2>>"$LOG" || warn "pip install partial"
                deactivate
                log "pos_exporter venv created with dependencies."
            fi
        fi
    fi

    chown -R "$OPERATOR:$OPERATOR" "$OPERATOR_HOME/Desktop"

    set_stage 5
    CURRENT=5
fi

# ================================================================
# STAGE 6: Sanctuary + AIDE baseline
# ================================================================
if [ "$CURRENT" -lt 6 ]; then
    log "Stage 6: Sanctuary + AIDE..."

    # Sanctuary skeleton
    mkdir -p /sanctuary/quarantine/pos /sanctuary/logs /sanctuary/baselines /sanctuary/config
    chown -R "$OPERATOR:$OPERATOR" /sanctuary
    chmod 700 /sanctuary/quarantine /sanctuary/config

    # Copy runbook into sanctuary for reference
    cp "$SCRIPT_DIR/INSTALL_POS.sh" /sanctuary/config/ 2>/dev/null || true

    # AIDE baseline (if installed)
    if command -v aide &>/dev/null; then
        log "Initializing AIDE baseline..."
        aide --init 2>>"$LOG" || warn "AIDE init had issues"
        cp /var/lib/aide/aide.db.new /var/lib/aide/aide.db 2>/dev/null || true
        log "AIDE baseline set. Any future changes are now detectable."
    else
        warn "AIDE not installed — skip baseline. Install later, run: aide --init"
    fi

    set_stage 6
    CURRENT=6
fi

# ================================================================
# CLEANUP
# ================================================================
rm -f "$PROGRESS"

echo "" | tee -a "$LOG"
echo "================================================================" | tee -a "$LOG"
echo "   POS SOVEREIGN INSTALLATION COMPLETE" | tee -a "$LOG"
echo "================================================================" | tee -a "$LOG"
echo "" | tee -a "$LOG"

log "Services:"
info "  Grafana:        $(systemctl is-active grafana-server 2>/dev/null || echo 'not running') — http://localhost:3000"
info "  Prometheus:     $(systemctl is-active prometheus 2>/dev/null || echo 'not running') — http://localhost:9090"
info "  node_exporter:  $(systemctl is-active node_exporter 2>/dev/null || echo 'not running') — :9100"
info "  UFW:            $(ufw status 2>/dev/null | head -1 || echo 'check manually')"
info "  Fail2ban:       $(systemctl is-active fail2ban 2>/dev/null || echo 'not running')"
info "  SSH:            key-only, no root"
echo "" | tee -a "$LOG"

log "Your 30-panel dashboard should be live at http://localhost:3000"
log "Default login: admin / admin"
log "pos_exporter: cd ~/Desktop/pos-command/grafana-stack && source venv/bin/activate && python3 pos_exporter.py"
log "Log: $LOG"
echo ""
