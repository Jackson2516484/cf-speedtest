#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 定时任务: 检查 data/ 下 IP 池的存活情况,
剔除失联 IP;若可用数量不足,从 Cloudflare 官方段补充新候选。
"""
import concurrent.futures
import ipaddress
import os
import socket
import ssl
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "data")
PROBE_DOMAIN = os.environ.get("PROBE_DOMAIN", "www.cloudflare.com")
MIN_KEEP = 60  # 每文件最少保留可用数,不足则补充


def alive(ip, timeout=6):
    try:
        raw = socket.create_connection((ip, 443), timeout=timeout)
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(raw, server_hostname=PROBE_DOMAIN)
        tls.close()
        return True
    except Exception:
        return False


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def fetch_ranges(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode().split()


def sample(cidr, count):
    net = ipaddress.ip_network(cidr)
    size = net.num_addresses
    idxs = set()
    for i in range(count):
        idx = 1 + int((i + 0.5) / count * (size - 2))
        idxs.add(max(1, min(idx, size - 2)))
    return [str(net[i]) for i in sorted(idxs)]


def check_all(ips, workers=30):
    ok = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(alive, ip): ip for ip in ips}
        for fut in concurrent.futures.as_completed(futs):
            if fut.result():
                ok.append(futs[fut])
    return ok


def process(name, ranges_url, per_range):
    path = os.path.join(DATA, name)
    ips = load(path)
    print(f"[{name}] 原有 {len(ips)} 个")
    good = check_all(ips)
    print(f"[{name}] 存活 {len(good)} 个")
    if len(good) < MIN_KEEP:
        fresh = []
        for c in fetch_ranges(ranges_url):
            fresh.extend(sample(c, per_range))
        fresh = [ip for ip in dict.fromkeys(fresh) if ip not in set(ips)]
        print(f"[{name}] 补充候选 {len(fresh)} 个")
        good += check_all(fresh)
        print(f"[{name}] 补充后存活 {len(good)} 个")
    good = sorted(set(good))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Cloudflare 边缘 IP 候选池,由 GitHub Actions 定期健康检查维护\n")
        f.write("\n".join(good) + "\n")
    print(f"[{name}] 已写回 {len(good)} 个")


def main():
    os.makedirs(DATA, exist_ok=True)
    process("cf-ips-v4.txt", "https://www.cloudflare.com/ips-v4", 10)
    process("cf-ips-v6.txt", "https://www.cloudflare.com/ips-v6", 6)


if __name__ == "__main__":
    main()
