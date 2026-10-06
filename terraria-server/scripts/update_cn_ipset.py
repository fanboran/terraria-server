#!/usr/bin/env python3
"""更新 Terraria 7777 端口地区白名单（中国大陆 + 香港 IP 段）。

数据源：APNIC 官方 delegated 文件（权威 IP 分配数据）
产出：  /etc/ipset-terraria.conf（ipset 恢复文件）+ 运行时 ipset
说明：  下载失败时保留现有白名单不动；手动例外写在 cn_hk_overrides.txt
"""
import ipaddress
import json
import os
import subprocess
import sys
import urllib.request

IPSET = "cn_hk"
URL = "https://ftp.apnic.net/stats/apnic/delegated-apnic-latest"
OVERRIDES = "/opt/terraria/scripts/cn_hk_overrides.txt"
SAVE = "/etc/ipset-terraria.conf"
PORTS = ("7777",)  # Terraria（TCP；UDP 保险起见一并白名单）


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fetch():
    print("下载 APNIC 分配数据 ...")
    req = urllib.request.Request(URL, headers={"User-Agent": "terraria-geoip/1.0"})
    data = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
    if "apnic|" not in data or len(data) < 100000:
        raise RuntimeError("下载数据异常（过短）")
    return data


def collect(data):
    nets = []
    cc_count = {}
    for line in data.splitlines():
        f = line.split("|")
        if len(f) < 7 or f[2] != "ipv4":
            continue
        cc = f[1]
        if cc not in ("CN", "HK"):
            continue
        start, count = f[3], int(f[4])
        first = ipaddress.ip_address(start)
        last = ipaddress.ip_address(int(first) + count - 1)
        nets.extend(ipaddress.summarize_address_range(first, last))
        cc_count[cc] = cc_count.get(cc, 0) + 1
    return nets, cc_count


def main():
    rules_only = "--rules-only" in sys.argv
    nets = []
    if not rules_only:
        # 1) 下载（失败保留现状）
        try:
            data = fetch()
        except Exception as e:
            print(f"!! 下载失败，保留现有白名单不动：{e}")
            sys.exit(1)
        # 2) 解析 CN + HK 段
        nets, cc_count = collect(data)
    # 3) 手动例外（海外玩家）
    if os.path.exists(OVERRIDES):
        for line in open(OVERRIDES, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    nets.append(ipaddress.ip_network(line, strict=False))
                    print("  手动例外:", line)
                except ValueError:
                    print("  !! 无效例外行，忽略:", line)

    if not rules_only:
        print(f"网段统计: CN={cc_count.get('CN', 0)} 段, HK={cc_count.get('HK', 0)} 段, "
              f"合并+例外后共 {len(nets)} 段")

    # 4) 原子写 ipset 恢复文件
    tmp = SAVE + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"create {IPSET} hash:net family inet hashsize 4096 maxelem 100000 -exist\n")
        f.write(f"flush {IPSET}\n")
        for n in nets:
            f.write(f"add {IPSET} {n}\n")
    os.replace(tmp, SAVE)

    # 5) 应用到运行时 ipset（先确保集合存在）
    if run(["ipset", "list", IPSET]).returncode != 0:
        run(["ipset", "create", IPSET, "hash:net", "family", "inet",
             "hashsize", "4096", "maxelem", "100000"])
    r = run(["ipset", "restore", "-exist"], input=open(SAVE).read())
    if r.returncode != 0:
        print("!! ipset restore 失败:", r.stderr[:300])
        sys.exit(1)
    cnt = run(["ipset", "list", IPSET]).stdout.count("\n")
    print("ipset 已更新（restore 成功）")

    # 6) 确保 iptables 规则存在（幂等；规则本体另在 ufw before.rules 中持久化）
    for proto in ("tcp", "udp"):
        match = ["-p", proto, "--dport", PORTS[0],
                 "-m", "set", "!", "--match-set", IPSET, "src", "-j", "DROP"]
        chk = run(["iptables", "-C", "INPUT"] + match)
        if chk.returncode != 0:
            ins = run(["iptables", "-I", "INPUT", "1"] + match)
            print(f"iptables {proto.upper()} 规则已插入" if ins.returncode == 0
                  else f"!! {proto} 规则插入失败: {ins.stderr[:200]}")
        else:
            print(f"iptables {proto.upper()} 规则已存在")

    print("完成。")


if __name__ == "__main__":
    main()
