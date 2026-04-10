#!/usr/bin/env bash
# setup-host.sh — Harden a fresh Ubuntu 24.04 server for running Roost
#
# Usage: ssh root@<IP> 'bash -s' < setup-host.sh
#    or: scp setup-host.sh root@<IP>:/tmp/ && ssh root@<IP> bash /tmp/setup-host.sh
#
# What it does:
#   1. Creates a non-root user with SSH key access
#   2. Installs Docker Engine
#   3. Configures UFW firewall
#   4. Installs fail2ban for SSH protection
#   5. Hardens SSH (pubkey only, no root login)
#   6. Applies kernel hardening (sysctl)
#   7. Enables unattended security upgrades

set -euo pipefail

# ── Configuration ──
# Override these via environment variables before running:
#   ROOST_USER=dev ROOST_SSH_PORT=22 bash setup-host.sh
ROOST_USER="${ROOST_USER:-dev}"
ROOST_SSH_PORT="${ROOST_SSH_PORT:-22}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; exit 1; }

[[ $EUID -eq 0 ]] || error "Must run as root"

echo ""
echo "=================================="
echo "  Roost — Host Setup & Hardening"
echo "=================================="
echo ""

# ── 1. System packages ──
info "Updating system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

# ── 2. Create user ──
if id "$ROOST_USER" &>/dev/null; then
    info "User $ROOST_USER already exists"
else
    info "Creating user $ROOST_USER..."
    adduser --disabled-password --gecos "Roost" "$ROOST_USER"
fi

# Passwordless sudo
echo "$ROOST_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$ROOST_USER"
chmod 440 "/etc/sudoers.d/$ROOST_USER"

# SSH keys — copy from root
HOMEDIR="/home/$ROOST_USER"
sudo -u "$ROOST_USER" mkdir -p "$HOMEDIR/.ssh"
chmod 700 "$HOMEDIR/.ssh"
if [[ -f /root/.ssh/authorized_keys ]]; then
    cp /root/.ssh/authorized_keys "$HOMEDIR/.ssh/authorized_keys"
    chown -R "$ROOST_USER:$ROOST_USER" "$HOMEDIR/.ssh"
    chmod 600 "$HOMEDIR/.ssh/authorized_keys"
    info "Copied SSH keys from root"
fi

# Enable lingering (systemd user services persist after logout)
loginctl enable-linger "$ROOST_USER"

# ── 3. Install Docker ──
if command -v docker &>/dev/null; then
    info "Docker already installed: $(docker --version)"
else
    info "Installing Docker Engine..."
    apt-get install -y -qq ca-certificates curl gnupg > /dev/null 2>&1
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin > /dev/null 2>&1
    info "Docker installed: $(docker --version)"
fi

# Add user to docker group
usermod -aG docker "$ROOST_USER"
info "Added $ROOST_USER to docker group"

# ── 4. UFW Firewall ──
info "Configuring firewall..."
apt-get install -y -qq ufw > /dev/null 2>&1
ufw --force reset > /dev/null 2>&1
ufw default deny incoming > /dev/null 2>&1
ufw default allow outgoing > /dev/null 2>&1
ufw allow "$ROOST_SSH_PORT/tcp" comment "SSH" > /dev/null 2>&1
ufw allow 80/tcp comment "HTTP" > /dev/null 2>&1
ufw allow 443/tcp comment "HTTPS" > /dev/null 2>&1
ufw --force enable > /dev/null 2>&1
info "UFW enabled: SSH($ROOST_SSH_PORT), HTTP(80), HTTPS(443)"

# ── 5. fail2ban ──
info "Configuring fail2ban..."
apt-get install -y -qq fail2ban > /dev/null 2>&1
cat > /etc/fail2ban/jail.local << EOF
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = $ROOST_SSH_PORT
mode = aggressive
maxretry = 3
bantime = 24h
EOF
systemctl enable fail2ban > /dev/null 2>&1
systemctl restart fail2ban
info "fail2ban enabled (3 strikes = 24h ban)"

# ── 6. SSH hardening ──
info "Hardening SSH..."
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/hardened.conf << EOF
MaxAuthTries 3
LoginGraceTime 20

PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey

X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding yes
PermitTunnel no

PermitRootLogin no
EOF

sshd -t && systemctl restart sshd || warn "sshd config error — check manually"
info "SSH hardened (pubkey only, no root login)"

# ── 7. Kernel hardening ──
info "Applying kernel hardening..."
cat > /etc/sysctl.d/99-hardened.conf << EOF
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
EOF
sysctl --system > /dev/null 2>&1

# ── 8. Unattended upgrades ──
info "Enabling unattended security upgrades..."
apt-get install -y -qq unattended-upgrades > /dev/null 2>&1
cat > /etc/apt/apt.conf.d/20auto-upgrades << EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# ── Done ──
echo ""
echo "=================================="
echo "  Host setup complete"
echo "=================================="
echo ""
echo "  User:     $ROOST_USER"
echo "  SSH:      ssh $ROOST_USER@$(hostname -I | awk '{print $1}')"
echo "  Docker:   $(docker --version)"
echo "  Firewall: UFW (SSH:$ROOST_SSH_PORT, HTTP:80, HTTPS:443)"
echo ""
echo "  Next: log in as $ROOST_USER and deploy Roost with Docker Compose"
echo ""
