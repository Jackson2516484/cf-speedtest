# CF 节点优选 IP 工具

给 Cloudflare Worker 节点自动挑选最快边缘 IP 的小工具。
测速在**你自己的电脑上**运行,测的是「你的网络 → 各个 Cloudflare IP → 你的节点」的真实速度,
挑出的 IP 填到客户端里,节点速度直接起飞。

## 工作原理

1. `data/` 下是候选 IP 池(从 Cloudflare 官方公布的 IP 段抽样),由 GitHub Actions 每 6 小时自动健康检查、剔除失联 IP、不足时补充。
2. 你在自己电脑上运行 `speedtest.py`,它对池中每个 IP 直连 443 端口、用你的节点域名做 SNI 完成 TLS 握手,
   再经该 IP 向你的节点请求测速文件(`/dl`),按**下载速度**(或延迟)排名。
3. 把第 1 名的 IP 填到客户端配置的 `address` 栏,SNI / host 保持节点域名不变,其他不用动。

## 本地使用(3 步)

```bash
# 1. 克隆仓库(或直接下载 speedtest.py + data/ 目录)
git clone https://github.com/<你的用户名>/cf-speedtest.git
cd cf-speedtest

# 2. 运行测速(把 example.com 换成你的节点域名: Worker 域名或自定义域)
python3 speedtest.py --domain <你的节点域名>

# 3. 按输出的排名,把第 1 名 IP 填进客户端的 address 栏
```

常用参数:

| 参数 | 说明 |
|---|---|
| `--top 10` | 输出前 10 名(默认 5) |
| `--ips ./my.txt` | 用自己的 IP 列表代替内置池 |
| `--pool-url <地址>` | 从 URL 获取最新 IP 池 |
| `--no-dl` | 只测延迟,不测下载速度(节点没部署 /dl 端点时用) |
| `--workers 30` | 并发数,默认 20 |
| `--timeout 8` | 单个 IP 超时秒数,默认 8 |

测速结果会保存为 `result-年月日-时分秒.txt`,建议同时记下第 2、3 名备用。

## 客户端配置方法(v2rayNG / Streisand 通用)

- `address`: 改成测出的最优 IP(例如 `104.21.33.5`)
- `port`: 443 不变
- `SNI` / `host` / 伪装域名: 保持你的节点域名不变(例如 `xxx.workers.dev` 或你的自定义域)
- 其余(UUID、传输方式 ws、TLS)都不用动

## 注意事项

- 优选 IP 解决的是「你的宽带到 Cloudflare 边缘」这一段的速度;Cloudflare 官方 Anycast 本来就会选近的 PoP,
  优选 IP 是在运营商 peer 不好时手动指定更优入口,提升因人而异,实测为准。
- 建议搭配**自定义域**使用:workers.dev 域名在部分网络下会被限速或污染,自定义域 + 优选 IP 效果最好。
- 免费版 Worker 每天 10 万次请求,正常个人使用足够;测速本身只产生几十次请求。
- IP 的快慢会随时间变化,感觉变慢时重新跑一次 `speedtest.py` 即可。
