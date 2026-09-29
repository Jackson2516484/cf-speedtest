#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare 节点优选 IP 测速工具
自动从 IP 池中找出对「你当前网络」最快的 Cloudflare 边缘 IP。

原理: 对池中每个候选 IP 直连 443 端口,用你的节点域名做 SNI 完成 TLS 握手,
随后经该 IP 向你的节点请求测速文件(/dl),综合握手延迟与下载速度给出排名。
测得的最优 IP 填到客户端的 address 栏,SNI / host 保持你的节点域名不变即可。

用法:
    python3 speedtest.py --domain <你的节点域名>
    python3 speedtest.py --domain <你的节点域名> --top 10
    python3 speedtest.py --domain <你的节点域名> --ips ./my-ips.txt
    python3 speedtest.py --domain <你的节点域名> --no-dl   # 只测延迟,不测下载速度

依赖: 仅 Python 3 标准库,无需安装任何包。
"""

import argparse
import concurrent.futures
import datetime
import os
import socket
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库公开后填入 IP 池的 raw 地址,即可实现"自动获取最新 IP 池"
DEFAULT_POOL_URLS = [
    "https://raw.githubusercontent.com/Jackson2516484/cf-speedtest/main/data/cf-ips-v4.txt",
    "https://raw.githubusercontent.com/Jackson2516484/cf-speedtest/main/data/cf-ips-v6.txt",
]
DL_SIZE = 2 * 1024 * 1024  # 测速文件 2MB


def load_ips(args):
    ips = []
    if args.ips:
        with open(args.ips, encoding="utf-8") as f:
            ips = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    elif args.pool_url or DEFAULT_POOL_URLS:
        for url in (args.pool_url or DEFAULT_POOL_URLS):
            print(f"正在从 {url} 获取最新 IP 池…")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            text = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
            ips += [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    else:
        for name in ("data/cf-ips-v4.txt", "data/cf-ips-v6.txt"):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    ips += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    seen, out = set(), []
    for ip in ips:
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def tls_handshake(ip, domain, timeout):
    """TCP + TLS(带 SNI)握手,返回 (tcp_ms, tls_ms, tls连接) 或抛异常。"""
    t0 = time.perf_counter()
    raw = socket.create_connection((ip, 443), timeout=timeout)
    tcp_ms = (time.perf_counter() - t0) * 1000
    ctx = ssl.create_default_context()
    t1 = time.perf_counter()
    try:
        tls = ctx.wrap_socket(raw, server_hostname=domain)
    except Exception:
        raw.close()
        raise
    tls_ms = (time.perf_counter() - t1) * 1000
    tls.settimeout(timeout)
    return tcp_ms, tls_ms, tls


def http_get(tls, domain, path):
    """经已建立的 TLS 连接发 HTTP/1.1 请求,返回 (状态行, body字节数, 首字节耗时ms, 总耗时s)。"""
    t0 = time.perf_counter()
    tls.sendall(
        f"GET {path} HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n".encode()
    )
    head = b""
    ttfb_ms = None
    while b"\r\n\r\n" not in head:
        chunk = tls.recv(4096)
        if not chunk:
            break
        if ttfb_ms is None:
            ttfb_ms = (time.perf_counter() - t0) * 1000
        head += chunk
    status = head.split(b"\r\n", 1)[0].decode("latin1", "ignore") if head else ""
    body_len = len(head.split(b"\r\n\r\n", 1)[1]) if b"\r\n\r\n" in head else 0
    while True:
        chunk = tls.recv(65536)
        if not chunk:
            break
        body_len += len(chunk)
    total_s = time.perf_counter() - t0
    return status, body_len, ttfb_ms or 0, total_s


def test_ip(ip, domain, timeout=8, use_dl=True):
    """返回 dict: ok / tcp_ms / tls_ms / ttfb_ms / mbps / error"""
    res = {"ip": ip, "ok": False}
    try:
        tcp_ms, tls_ms, tls = tls_handshake(ip, domain, timeout)
    except Exception as e:
        res["error"] = f"握手失败({type(e).__name__})"
        return res
    res["tcp_ms"], res["tls_ms"] = tcp_ms, tls_ms
    try:
        _status, _n, ttfb_ms, _t = http_get(tls, domain, "/cdn-cgi/trace")
        res["ttfb_ms"] = ttfb_ms
    except Exception as e:
        res["error"] = f"HTTP失败({type(e).__name__})"
        return res
    finally:
        try:
            tls.close()
        except Exception:
            pass
    if use_dl:
        try:
            _t0, _t1, tls2 = tls_handshake(ip, domain, timeout)
            try:
                status, body_len, _tt, total_s = http_get(tls2, domain, f"/dl?size={DL_SIZE}")
                if "404" in status:
                    res["mbps"] = None  # 节点未部署测速端点,降级为延迟排名
                elif body_len > 0 and total_s > 0:
                    res["mbps"] = round(body_len * 8 / 1e6 / total_s, 2)
                else:
                    res["mbps"] = None
            finally:
                tls2.close()
        except Exception:
            res["mbps"] = None
    else:
        res["mbps"] = None
    res["ok"] = True
    return res


def fmt(v, width=8):
    return f"{v:>{width}.1f}" if isinstance(v, (int, float)) else f"{'-':>{width}}"


def main():
    ap = argparse.ArgumentParser(description="Cloudflare 节点优选 IP 测速工具")
    ap.add_argument("--domain", required=True, help="你的节点域名(Worker 域名或自定义域)")
    ap.add_argument("--ips", help="本地 IP 列表文件(默认用仓库 data/ 下的池)")
    ap.add_argument("--pool-url", action="append", default=None,
                        help="从 URL 获取最新 IP 池(可重复指定,v4/v6 各一个)")
    ap.add_argument("--top", type=int, default=5, help="输出前 N 名 (默认 5)")
    ap.add_argument("--workers", type=int, default=20, help="并发数 (默认 20)")
    ap.add_argument("--timeout", type=int, default=8, help="单个 IP 超时秒数 (默认 8)")
    ap.add_argument("--no-dl", action="store_true", help="只测延迟,不测下载速度")
    args = ap.parse_args()

    ips = load_ips(args)
    if not ips:
        print("IP 池为空,请检查 data/ 目录或 --ips 参数。")
        sys.exit(1)
    print(f"共 {len(ips)} 个候选 IP,目标域名 {args.domain},开始测速…\n")

    results, done = [], [0]
    use_dl = not args.no_dl
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(test_ip, ip, args.domain, args.timeout, use_dl): ip for ip in ips}
            for fut in concurrent.futures.as_completed(futs):
                done[0] += 1
                r = fut.result()
                results.append(r)
                mark = "✓" if r["ok"] else "✗"
                print(f"\r[{done[0]}/{len(ips)}] {mark} {r['ip']:<40}", end="", flush=True)
    except KeyboardInterrupt:
        print("\n\n用户中断,按已测结果排名。")
    print("\n")

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    print(f"可用 {len(ok)} 个,不可用 {len(bad)} 个。")
    if not ok:
        print("没有可用 IP,请检查网络或域名是否正确。")
        sys.exit(1)

    has_speed = any(r.get("mbps") for r in ok)
    if has_speed:
        ok.sort(key=lambda r: (-(r.get("mbps") or 0), r["ttfb_ms"]))
        rank_key = "下载速度"
    else:
        if use_dl:
            print("注: 节点未返回测速文件(可能未部署 /dl 端点),改按延迟排名。\n")
        ok.sort(key=lambda r: r["ttfb_ms"])
        rank_key = "延迟"

    print(f"===== 优选排名(按{rank_key}) =====\n")
    print(f"{'排名':<4} {'IP':<40} {'TCPms':>7} {'TLSms':>7} {'TTFBms':>7} {'Mbps':>8}")
    for i, r in enumerate(ok[: args.top], 1):
        print(f"{i:<4} {r['ip']:<40} {fmt(r['tcp_ms'],7)} {fmt(r['tls_ms'],7)} "
              f"{fmt(r['ttfb_ms'],7)} {fmt(r.get('mbps'),8)}")

    # 保存完整结果
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(HERE, f"result-{ts}.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 测速时间 {ts}  域名 {args.domain}  按{rank_key}排名\n")
        for i, r in enumerate(ok, 1):
            f.write(f"{i}. {r['ip']}  tcp={r['tcp_ms']:.1f}ms tls={r['tls_ms']:.1f}ms "
                    f"ttfb={r['ttfb_ms']:.1f}ms mbps={r.get('mbps')}\n")
    print(f"\n完整排名已保存到 {out}")
    print("\n使用方法: 把客户端配置里的 address(地址) 改成第 1 名的 IP,")
    print("SNI / host / 伪装域名保持你的节点域名不变,其他不用动。")
    print("建议同时记下第 2、3 名备用。")


if __name__ == "__main__":
    main()
