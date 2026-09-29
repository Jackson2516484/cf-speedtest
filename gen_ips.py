#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Cloudflare 官方公布的 IP 段中按确定性规则抽样,生成候选 IP 池。"""
import ipaddress
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read().decode().split()


def sample(cidr, count):
    net = ipaddress.ip_network(cidr)
    size = net.num_addresses
    idxs = set()
    for i in range(count):
        idx = 1 + int((i + 0.5) / count * (size - 2))
        idxs.add(max(1, min(idx, size - 2)))
    return [str(net[i]) for i in sorted(idxs)]


def main():
    v4, v6 = fetch("https://www.cloudflare.com/ips-v4"), fetch("https://www.cloudflare.com/ips-v6")
    print(f"官方 v4 段 {len(v4)} 个, v6 段 {len(v6)} 个")
    v4_ips, v6_ips = [], []
    for c in v4:
        v4_ips.extend(sample(c, 10))
    for c in v6:
        v6_ips.extend(sample(c, 6))
    v4_ips = sorted(set(v4_ips), key=lambda ip: tuple(int(x) for x in ip.split(".")))
    v6_ips = sorted(set(v6_ips))
    with open("data/cf-ips-v4.txt", "w", encoding="utf-8") as f:
        f.write("# Cloudflare 边缘 IP 候选池 (v4), 由 GitHub Actions 定期健康检查维护\n")
        f.write("\n".join(v4_ips) + "\n")
    with open("data/cf-ips-v6.txt", "w", encoding="utf-8") as f:
        f.write("# Cloudflare 边缘 IP 候选池 (v6), 由 GitHub Actions 定期健康检查维护\n")
        f.write("\n".join(v6_ips) + "\n")
    print(f"生成 v4 {len(v4_ips)} 个, v6 {len(v6_ips)} 个")


if __name__ == "__main__":
    main()
