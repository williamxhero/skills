---
name: yosef-server
description: Reuse connection, storage, network, and ops facts for the user's small Ubuntu server. Use when the user mentions 小电脑, yosef-server, SSH/direct Ethernet access, /data, services, downloads, data placement, or managing this machine.
---

# Yosef Server

## 固定事实

- `yosef-server` 是用户的小电脑：全新私有 Ubuntu 24.04 服务器。
- 默认连接：`ssh yosef-server`。
- 日常访问必须走直连网线，不用 Wi-Fi 地址。
- 本机 hosts：`yosef-server -> 192.168.50.2`。
- 本机以太网：`192.168.50.1/24`；小电脑 `eno1`：`192.168.50.2/24`。
- 小电脑 Wi-Fi 网卡：`wlp3s0`；当前地址 `192.168.1.58/24`，仅用于上网默认路由和必要临时维护。
- 网络由 netplan + `systemd-networkd` 管理，未安装 `nmcli`。
- 直连网线配置：`/etc/netplan/60-wired-direct.yaml`。
- 本机 SSH 对 `yosef-server` 配置了 `BindAddress 192.168.50.1`，强制从直连网线连接。
- 本机 SSH 公钥已加入小电脑 `yosef` 用户 `authorized_keys`。
- 用户：`yosef`；临时密码登录密码也是 `yosef`。
- `yosef` 当前仍需密码 sudo；远程维护时默认按需输入密码 `yosef`。

## 目录约定

- `/data` 是统一工作根目录（不是 /volume）
- 服务安装、运行数据、应用数据、项目数据、下载文件、生成产物、长期数据，默认放在 `/data` 下。
- 不要把大量文件直接放在 `/data` 根目录；新增服务前先按职责规划子目录。
- 服务目录、配置目录、数据目录、下载目录，都优先规划到 `/data` 对应子目录。
- 仅系统强制要求时使用系统路径，如 systemd unit、sudoers、netplan。
- 中间产物和最终产物必须分目录、分文件管理，避免混写。

## 操作规则

- 连接和引用主机时优先用 `yosef-server`。
- 若 `ssh yosef-server` 失败，先检查：hosts、SSH 配置、以太网链路、`eno1` 地址、netplan 状态，再报告用户。
- 远端是 Ubuntu；远端命令默认用 Linux shell，不用 Windows 路径、PowerShell 或 Windows 计划任务习惯。
- 复杂引号、脚本或多步操作：优先写脚本再执行；写入后回读，创建后实跑验证。
- 不假设重装前 Docker、端口服务或旧目录仍存在；使用前必须先确认。
- 只讨论主机本身，除非用户明确询问其上运行的服务。

## 提供服务
- Task Center http://yosef-server:8810/readme 
- MarketHub http://yosef-server:8803/api/openapi.json
- 爬虫文章 http://yosef-server:8815/
- SearXNG http://yosef-server:8801/
- CPA http://yosef-server:8317/

## 快速检查

```bash
ssh yosef-server
ip -br addr show eno1
ip route
cat /etc/netplan/60-wired-direct.yaml
```
